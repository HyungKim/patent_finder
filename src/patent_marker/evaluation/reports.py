"""모델 평가 보고서 (스펙 12절).

- segment 수준(운영 단위)과 문단 수준(max 집계 후 최종 표시) 지표를 모두 낸다.
- 평가 partition의 문서 계열이 학습에 쓰였는지(누수) 검사한다.
- 파서가 읽지 못해 사람이 등록한 누락 후보는 end-to-end Recall에서 FN으로 센다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..classifiers.bundle import ModelBundle
from ..classifiers.registry import get_model, load_registered_bundle
from ..feedback.snapshot import PARTITIONS, find_snapshot, load_snapshot
from ..policies.aggregation import paragraph_truth
from ..policies.thresholds import get_policy
from ..runtime import atomic_write_json, atomic_write_text, sha256_file, utc_now
from ..scoring import ensure_compatible, score_segments
from ..services import Services
from . import metrics


class EvaluationError(RuntimeError):
    pass


def parse_split(spec: str) -> tuple[str, str]:
    """'validation-v1' → ('validation', 'v1')."""
    partition, _, tag = spec.partition("-")
    if partition not in PARTITIONS or not tag:
        raise EvaluationError("--split은 '<train|validation|test>-<tag>' 형식이어야 합니다 (예: validation-v1).")
    return partition, tag


def paragraph_units(services: Services, bundle: ModelBundle, rows: list[dict[str, Any]],
                    scores: np.ndarray) -> list[dict[str, Any]]:
    """snapshot 행이 속한 문단들의 (정답, max 점수). 정답을 정할 수 없는 문단은 제외한다."""
    by_paragraph: dict[str, dict[str, Any]] = {}
    known_scores = {row["segment_id"]: float(score) for row, score in zip(rows, scores)}
    labels = {row["segment_id"]: row["label"] for row in rows}
    for row in rows:
        by_paragraph.setdefault(row["paragraph_id"], {"row": row})
    paragraph_ids = sorted(by_paragraph)
    siblings: list[dict[str, Any]] = []
    for start in range(0, len(paragraph_ids), 400):
        chunk = paragraph_ids[start: start + 400]
        for item in services.database.query(
            "SELECT segment_id, paragraph_id, normalized_text, context_segment_ids_json FROM segments "
            f"WHERE paragraph_id IN ({','.join('?' * len(chunk))}) ORDER BY paragraph_id, segment_index", chunk,
        ):
            siblings.append({"segment_id": item["segment_id"], "paragraph_id": item["paragraph_id"],
                             "normalized_text": item["normalized_text"],
                             "context_segment_ids": json.loads(item["context_segment_ids_json"])})
    unscored = [item for item in siblings if item["segment_id"] not in known_scores]
    if unscored:
        extra, _ = score_segments(services, bundle, unscored)
        known_scores.update({item["segment_id"]: float(score) for item, score in zip(unscored, extra)})
    units = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in siblings:
        grouped.setdefault(item["paragraph_id"], []).append(item)
    for paragraph_id, members in grouped.items():
        truth = paragraph_truth([labels.get(member["segment_id"]) for member in members])
        if truth is None:
            continue
        row = by_paragraph[paragraph_id]["row"]
        units.append({
            "paragraph_id": paragraph_id, "truth": 1 if truth == "YES" else 0,
            "score": max(known_scores[member["segment_id"]] for member in members),
            "family": row["document_family_id"], "document_id": row["document_id"],
        })
    return units


def _level_report(truth: np.ndarray, scores: np.ndarray, threshold: float | None, groups: list[str],
                  documents: list[str], samples: int, seed: int) -> dict[str, Any]:
    report: dict[str, Any] = {"average_precision": metrics.average_precision(truth, scores),
                              "n_positive": int(np.sum(truth)), "n": int(len(truth))}
    if threshold is None:
        report["at_threshold"] = None
        return report
    flagged = scores >= threshold
    at = metrics.binary_metrics(truth, flagged)
    at["recall_ci95"] = metrics.group_bootstrap_ci(truth, flagged, groups, samples, seed, "recall")
    at["precision_ci95"] = metrics.group_bootstrap_ci(truth, flagged, groups, samples, seed, "precision")
    at["document_candidate_recall"] = metrics.document_candidate_recall(truth, flagged, documents)
    report["at_threshold"] = at
    return report


def evaluate_model(services: Services, model_version: str, split_spec: str,
                   output: Path | None = None) -> dict[str, Any]:
    database, config = services.database, services.config
    partition, tag = parse_split(split_spec)
    registered = get_model(database, model_version)
    if registered is None:
        raise EvaluationError(f"registry에 없는 모델입니다: {model_version}")
    bundle = load_registered_bundle(database, model_version, config.base_dir)
    ensure_compatible(services, bundle)
    policy = get_policy(database, registered["policy_version"])
    threshold = policy["threshold"] if policy else None

    snapshot = load_snapshot(database, find_snapshot(database, tag))
    rows = [row for row in snapshot["rows"] if row["partition"] == partition]
    if not rows:
        raise EvaluationError(f"{split_spec}에 평가할 라벨이 없습니다.")

    # ---- 누수 검사 ----
    train_families = set(bundle.training.get("train_families") or [])
    eval_families = {row["document_family_id"] for row in rows}
    overlap = sorted(train_families & eval_families)
    train_hashes = set(bundle.training.get("train_text_hashes") or [])
    duplicate_texts = sum(1 for row in rows if row["text_hash"] in train_hashes)
    leakage = {
        "ok": not overlap and partition != "train", "overlapping_families": overlap,
        "evaluated_on_training_partition": partition == "train",
        "exact_duplicate_texts_in_train": duplicate_texts,
    }

    truth = np.array([1 if row["label"] == "YES" else 0 for row in rows])
    scores, _ = score_segments(services, bundle, rows)
    groups = [row["document_family_id"] for row in rows]
    documents = [row["document_id"] for row in rows]
    samples, seed = config.evaluation.bootstrap_samples, config.classifier.random_seed
    segment_level = _level_report(truth, scores, threshold, groups, documents, samples, seed)

    units = paragraph_units(services, bundle, rows, scores)
    p_truth = np.array([unit["truth"] for unit in units])
    p_scores = np.array([unit["score"] for unit in units])
    paragraph_level = _level_report(p_truth, p_scores, threshold, [u["family"] for u in units],
                                    [u["document_id"] for u in units], samples, seed)

    # ---- 라벨 완전성과 end-to-end Recall ----
    document_ids = sorted(set(documents))
    marks = ",".join("?" * len(document_ids))
    total_segments = {row["document_id"]: row["n"] for row in database.query(
        f"SELECT document_id, COUNT(*) AS n FROM segments WHERE document_id IN ({marks}) GROUP BY document_id",
        document_ids)}
    reviewed = {row["document_id"]: (row["n"], row["hold"] or 0) for row in database.query(
        "SELECT s.document_id, COUNT(*) AS n, SUM(rl.label = 'HOLD') AS hold FROM resolved_labels rl "
        "JOIN segments s ON s.segment_id = rl.target_id "
        f"WHERE rl.target_type = 'segment' AND s.document_id IN ({marks}) GROUP BY s.document_id", document_ids)}
    fully_labeled = [d for d in document_ids if reviewed.get(d, (0, 0))[0] >= total_segments.get(d, 0)]
    hold_segments = int(sum(value[1] for value in reviewed.values()))
    unparsed = database.query_one(
        f"SELECT COUNT(*) FROM unparsed_positives WHERE retracted_at IS NULL AND document_id IN ({marks})",
        document_ids)[0]
    end_to_end = None
    if threshold is not None and segment_level["at_threshold"]:
        at = segment_level["at_threshold"]
        end_to_end = {
            "tp": at["tp"], "fn_segments": at["fn"], "fn_unparsed": int(unparsed),
            "recall": metrics.safe_div(at["tp"], at["tp"] + at["fn"] + unparsed),
        }

    slices: dict[str, Any] = {}
    false_negatives: list[dict[str, Any]] = []
    if threshold is not None:
        flagged = scores >= threshold
        slices = {
            "format": metrics.sliced_metrics(truth, flagged, [row["format"] for row in rows]),
            "language": metrics.sliced_metrics(truth, flagged, [metrics.language_mix(row["normalized_text"]) for row in rows]),
            "length": metrics.sliced_metrics(truth, flagged, [metrics.length_bucket(row["token_count"]) for row in rows]),
            "table": metrics.sliced_metrics(truth, flagged, ["table_row" if row["kind"] == "table_row" else "text" for row in rows]),
            "label_source": metrics.sliced_metrics(truth, flagged, ["implicit" if row["implicit"] else "explicit" for row in rows]),
        }
        for row, score, is_flagged in zip(rows, scores, flagged):
            if row["label"] == "YES" and not is_flagged:
                false_negatives.append({"segment_id": row["segment_id"], "document_id": row["document_id"],
                                        "score": float(score), "text": row["normalized_text"][:200]})

    previous_tests = database.query_one(
        "SELECT COUNT(*) FROM evaluations WHERE split_tag = ? AND partition = 'test'", (tag,))[0]
    report = {
        "schema_version": 1, "created_at": utc_now(), "model_version": model_version,
        "model_kind": bundle.kind, "artifact_sha256": registered["artifact_sha256"],
        "policy_version": registered["policy_version"], "policy_status": policy["status"] if policy else "UNVALIDATED",
        "threshold": threshold, "threshold_selected_on_this_partition": bool(
            policy and policy["selection"].get("basis") == "validation_partition" and partition == "validation"
            and policy["selection"].get("split_tag") == tag),
        "split": {"spec": split_spec, "partition": partition, "tag": tag,
                  "snapshot_id": snapshot["snapshot_id"], "label_snapshot_hash": snapshot["label_snapshot_hash"],
                  "split_manifest_hash": snapshot["split_manifest_hash"]},
        "independent": partition == "test",
        "counts": {"segments": len(rows), "positive_segments": int(truth.sum()),
                   "negative_segments": int(len(truth) - truth.sum()), "document_families": len(eval_families),
                   "documents": len(document_ids), "implicit_labels": sum(row["implicit"] for row in rows),
                   "paragraphs": len(units)},
        "leakage": leakage,
        "segment_level": segment_level, "paragraph_level": paragraph_level,
        "end_to_end": end_to_end,
        "label_completeness": {"documents": len(document_ids), "fully_labeled_documents": len(fully_labeled),
                               "labeled_segments": len(rows), "hold_segments": hold_segments,
                               "total_segments": int(sum(total_segments.values())),
                               "note": "일부만 라벨링된 문서가 있으면 Recall은 모집단 추정이 아니다."},
        "calibration": metrics.calibration(truth, scores),
        "slices": slices, "false_negatives": false_negatives,
        "test_partition_previous_evaluations": int(previous_tests),
        "feature_mode": bundle.mode, "encoder_revision": bundle.encoder_revision,
    }

    sequence = database.query_one("SELECT COUNT(*) FROM evaluations")[0] + 1
    path = Path(output) if output else config.artifacts_dir / f"eval-{sequence:04d}.json"
    atomic_write_json(path, report)
    atomic_write_text(path.with_suffix(".md"), render_markdown(report))
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO evaluations (evaluation_id, model_version, policy_version, split_tag, partition, "
            "report_path, report_sha256, summary_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"eval-{sequence:04d}", model_version, registered["policy_version"], tag, partition, str(path),
             sha256_file(path), json.dumps({"recall": (segment_level["at_threshold"] or {}).get("recall")}),
             utc_now()),
        )
    report["report_path"] = str(path)
    return report


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# 평가 보고서: {report['model_version']} / {report['split']['spec']}",
        "",
        f"- 생성: {report['created_at']}",
        f"- 정책: {report['policy_version']} ({report['policy_status']}), threshold = {_fmt(report['threshold'])}",
        f"- 독립 평가 집합 여부: {'예 (test)' if report['independent'] else '아니오'}",
        f"- 누수 검사: {'통과' if report['leakage']['ok'] else '실패'}"
        f" (겹치는 문서 계열 {len(report['leakage']['overlapping_families'])}개,"
        f" 학습과 동일한 문장 {report['leakage']['exact_duplicate_texts_in_train']}건)",
        f"- 표본: segment {report['counts']['segments']}개 (YES {report['counts']['positive_segments']},"
        f" NO {report['counts']['negative_segments']}), 문서 계열 {report['counts']['document_families']}개",
        "",
        "후보 점수는 특허 등록 확률이 아니다. 아래 수치는 이 평가 집합에서 측정한 값이다.",
        "",
        "| 수준 | Recall | 95% CI | Precision | F2 | 검토 비율 | TP | FN | FP | AP |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, key in (("segment", "segment_level"), ("문단(max)", "paragraph_level")):
        level = report[key]
        at = level.get("at_threshold")
        if not at:
            lines.append(f"| {name} | N/A | N/A | N/A | N/A | N/A | - | - | - | {_fmt(level['average_precision'])} |")
            continue
        ci = at.get("recall_ci95")
        ci_text = f"{ci['low']:.3f}–{ci['high']:.3f}" if ci else "N/A"
        lines.append(
            f"| {name} | {_fmt(at['recall'])} | {ci_text} | {_fmt(at['precision'])} | {_fmt(at['f2'])} | "
            f"{_fmt(at['review_ratio'])} | {at['tp']} | {at['fn']} | {at['fp']} | {_fmt(level['average_precision'])} |"
        )
    e2e = report.get("end_to_end")
    if e2e:
        lines += ["", f"- end-to-end Recall(파싱 누락 {e2e['fn_unparsed']}건 포함): {_fmt(e2e['recall'])}"]
    completeness = report["label_completeness"]
    lines += [
        f"- 라벨 완전성: 문서 {completeness['documents']}개 중 {completeness['fully_labeled_documents']}개가 전체 검토됨"
        f" (YES/NO {completeness['labeled_segments']}, HOLD {completeness['hold_segments']}, 전체 segment {completeness['total_segments']})",
    ]
    cal = report.get("calibration")
    if cal:
        lines.append(f"- 보정 품질: Brier {_fmt(cal['brier'])}, ECE {_fmt(cal['ece'])}")
    if report["test_partition_previous_evaluations"]:
        lines.append(f"- 주의: 이 test partition은 이전에 {report['test_partition_previous_evaluations']}번 평가에 쓰였다."
                     " 반복 조정에 썼다면 validation으로 재분류하고 새 test를 확보해야 한다.")
    if report["false_negatives"]:
        lines += ["", "## 놓친 후보 (FN)", ""]
        for item in report["false_negatives"][:50]:
            lines.append(f"- `{item['segment_id']}` (점수 {item['score']:.3f}) {item['text']}")
    return "\n".join(lines) + "\n"
