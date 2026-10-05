"""분석 run: 수집 → 임베딩 → 1차 후보 점수 → 판정 저장 (스펙 3절, 10절).

- run은 시작 시점의 모델·정책 버전을 끝까지 쓴다. 중단된 run을 재개해도 버전이 바뀌지 않는다.
- 예측은 (run, segment)당 한 번만 저장하므로 재개해도 중복되지 않는다.
- 운영 모델이 없으면 UNTRAINED로 끝나며 점수와 판정은 null이다(0점·NO로 바꾸지 않는다).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from .classifiers.seed import ensure_seed_model
from .existing_marks import describe_marks, total_marks
from .ingest import IngestResult, discover_files, ingest_file
from .policies.thresholds import decide
from .runtime import code_commit, environment_manifest, environment_manifest_hash, utc_now
from .scoring import load_active, load_for_run, score_segments
from .services import Services


class RunError(RuntimeError):
    pass


def _record_document(services: Services, run_id: str, result: IngestResult) -> None:
    with services.database.transaction() as connection:
        connection.execute(
            "INSERT INTO run_documents (run_id, document_id, input_path, status, error) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (run_id, input_path) DO UPDATE SET document_id = excluded.document_id, "
            "status = excluded.status, error = excluded.error",
            (run_id, result.document_id, str(result.path), result.status, result.error),
        )


def predict_document(services: Services, run_id: str, document_id: str, model: dict[str, Any]) -> int:
    """이 run에서 아직 예측하지 않은 segment만 점수를 계산해 저장한다."""
    rows = services.database.query(
        "SELECT segment_id, normalized_text, context_segment_ids_json FROM segments "
        "WHERE document_id = ? AND segment_id NOT IN (SELECT segment_id FROM predictions WHERE run_id = ?) "
        "ORDER BY seq", (document_id, run_id),
    )
    if not rows:
        return 0
    segments = [{"segment_id": row["segment_id"], "normalized_text": row["normalized_text"],
                 "context_segment_ids": json.loads(row["context_segment_ids_json"])} for row in rows]
    scores, embedding_ids = score_segments(services, model["bundle"], segments)
    policy = model["policy"] or {}
    threshold = policy.get("threshold")
    now = utc_now()
    records = []
    for segment, score, embedding_id in zip(segments, scores, embedding_ids):
        decision = decide(float(score), threshold)
        records.append((
            f"pred-{run_id}-{segment['segment_id']}", run_id, segment["segment_id"], embedding_id,
            model["model_version"], float(score), decision, threshold, model["policy_version"], decision, now,
        ))
    with services.database.transaction() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO predictions (prediction_id, run_id, segment_id, embedding_id, classifier_version, "
            "stage1_score, stage1_decision, threshold, policy_version, final_decision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", records,
        )
    return len(records)


def run_analysis(services: Services, input_path: Path, run_id: str, encoding: str | None = None,
                 progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    say = progress or (lambda message: None)
    database, config = services.database, services.config
    files = discover_files(Path(input_path))
    if not files:
        raise RunError(f"분석할 파일이 없습니다: {input_path}")

    existing = database.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if existing is not None:
        if existing["status"] in ("COMPLETED", "UNTRAINED"):
            raise RunError(f"이미 완료된 run입니다: {run_id}. 결과를 다시 만들려면 export 명령을 쓰세요.")
        model = None
        if existing["classifier_version"]:
            model = load_for_run(services, existing["classifier_version"], existing["policy_version"],
                                 existing["model_stage"])
        say(f"중단된 run을 이어서 진행합니다: {run_id}")
        with database.transaction() as connection:
            connection.execute("UPDATE runs SET status = 'RUNNING' WHERE run_id = ?", (run_id,))
    else:
        if services.store is not None:
            ensure_seed_model(services)
        model = load_active(services) if services.store is not None else None
        manifest = environment_manifest()
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO runs (run_id, kind, config_hash, code_commit, environment_manifest_hash, started_at, "
                "status, classifier_version, policy_version, model_stage, details_json) "
                "VALUES (?, 'analyze', ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?)",
                (run_id, config.config_hash(), code_commit(config.base_dir), environment_manifest_hash(manifest),
                 utc_now(), model["model_version"] if model else None, model["policy_version"] if model else None,
                 model["stage"] if model else None,
                 json.dumps({"input": str(Path(input_path).resolve()), "python": manifest["python"],
                             "platform": manifest["platform"]}, ensure_ascii=False)),
            )

    started = time.perf_counter()
    summary: dict[str, Any] = {"files": [], "segments": 0, "predicted": 0}
    per_segment_ms: list[float] = []
    try:
        for index, path in enumerate(files, start=1):
            say(f"[{index}/{len(files)}] {path.name}")
            result = ingest_file(services, path, encoding)
            _record_document(services, run_id, result)
            if total_marks(result.existing_marks):
                say(f"    기존 마킹 {total_marks(result.existing_marks)}건({describe_marks(result.existing_marks)})을 "
                    "걷어내고 시작합니다.")
            predicted = 0
            if result.document_id and result.status in ("ok", "partial") and model is not None:
                tick = time.perf_counter()
                predicted = predict_document(services, run_id, result.document_id, model)
                if predicted:
                    per_segment_ms.append((time.perf_counter() - tick) * 1000 / predicted)
            services.memory.rss_bytes()
            summary["files"].append({
                "path": str(path), "document_id": result.document_id, "status": result.status,
                "error": result.error, "segments": result.segments, "predicted": predicted,
                "existing_marks": result.existing_marks,
            })
            summary["segments"] += result.segments
            summary["predicted"] += predicted
    except BaseException:
        with database.transaction() as connection:
            connection.execute("UPDATE runs SET status = 'INTERRUPTED' WHERE run_id = ?", (run_id,))
        raise

    per_segment_ms.sort()
    details = json.loads(database.query_one("SELECT details_json FROM runs WHERE run_id = ?", (run_id,))[0])
    details.update({
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "files": len(files),
        "segments": summary["segments"],
        "memory": services.memory.summary(),
        "embedding_cache": dict(services.store.stats) if services.store else None,
        # 문서별 평균 지연의 분포. segment 단위 p50/p95는 benchmark 명령으로 측정한다.
        "per_segment_ms_by_document": {
            "p50": per_segment_ms[len(per_segment_ms) // 2] if per_segment_ms else None,
            "max": per_segment_ms[-1] if per_segment_ms else None,
        },
    })
    status = "COMPLETED" if model is not None else "UNTRAINED"
    with database.transaction() as connection:
        connection.execute("UPDATE runs SET status = ?, finished_at = ?, details_json = ? WHERE run_id = ?",
                           (status, utc_now(), json.dumps(details, ensure_ascii=False), run_id))
    summary.update({"run_id": run_id, "status": status, "model_version": model["model_version"] if model else None,
                    "policy_version": model["policy_version"] if model else None,
                    "stage": model["stage"] if model else None, "details": details})
    return summary
