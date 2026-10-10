"""1차 후보를 2단계 모델로 다시 판단해 나란히 비교하는 자료를 만든다 (실험용, 스펙 8.2의 shadow).

- 1차 판정과 기존 결과물(report.html, results.jsonl, marked/)은 바꾸지 않는다.
- 2단계 결과는 같은 1차 prediction ID에 덧붙여 second_stage_results에 저장한다.
- 2단계가 '아니다'로 본 후보를 뺀 사본은 비교용으로 따로 만든다. 2단계가 실패했거나 실행되지 않은 후보,
  사람이 YES로 판정한 구간은 빼지 않는다.
"""
from __future__ import annotations

import hashlib
import html
import json
import time
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from ..config import AppConfig
from ..export.annotations import annotate_documents
from ..export.data import ExportError, load_run, target_text
from ..runtime import atomic_write_text, utc_now
from ..segmentation.offsets import merge_spans
from ..storage import Database
from .adapter import Assessment

SAMPLING_REASON = "stage1_candidates_all"  # 1차 후보 전부를 본다. 1차가 놓친 구간은 보지 않는다


class BatchAdapter(Protocol):
    model_version: str
    question_version: str
    agree_at: float

    def assess_batch(self, items: Sequence[tuple[str, str]]) -> list[Assessment]:
        ...


def _candidates(run: dict[str, Any]) -> list[dict[str, Any]]:
    """1차가 후보로 본 구간. 문서 순서대로."""
    found = []
    for document in run["documents"]:
        for paragraph in document["paragraphs"]:
            for segment in paragraph["segments"]:
                if segment["final_decision"] == "CANDIDATE" and segment["prediction_id"]:
                    found.append({"document": document, "paragraph": paragraph, "segment": segment})
    return found


def _result_id(prediction_id: str, model_version: str, question_version: str) -> str:
    """같은 prediction을 같은 모델·질문으로 다시 판단하면 같은 행을 새 결과로 바꾼다."""
    digest = hashlib.sha256(f"{prediction_id}|{model_version}|{question_version}".encode("utf-8")).hexdigest()
    return f"s2-{digest[:24]}"


def _score(assessment: Assessment) -> float | None:
    values = [value for value in assessment.raw_scores.values() if isinstance(value, float)]
    return values[0] if values else None


def _verdict(assessment: Assessment) -> str:
    if assessment.status != "ok":
        return "판단 못 함"
    return "동의" if assessment.decision == "AGREE" else "이견"


def _report(run_id: str, adapter: BatchAdapter, rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return html.escape("" if value is None else str(value))

    body = []
    for row in sorted(rows, key=lambda item: (item["laya_score"] is None, item["laya_score"] or 0.0)):
        score = "" if row["laya_score"] is None else f"{row['laya_score']:.2f}"
        body.append(
            f"<tr class=\"{cell(row['laya_decision'] or 'none').lower()}\"><td>{cell(row['file_name'])}</td>"
            f"<td>{cell(row['location'])}</td><td>{cell(row['text'])}</td><td class=\"n\">{row['stage1_score']:.2f}</td>"
            f"<td class=\"n\">{score}</td><td>{cell(row['verdict'])}</td><td>{cell(row['human_label'] or '')}</td></tr>"
        )
    return (
        "<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        f"<title>Laya 비교 · {cell(run_id)}</title>\n<style>\n"
        "body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;margin:24px;line-height:1.6;color:#1b2420}\n"
        "table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #cfd6d1;padding:6px 9px;"
        "text-align:left;vertical-align:top}th{background:#e8efe9}\n"
        ".n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}\n"
        "tr.disagree td{background:#fbeeea}.note{background:#fbf0d5;border-left:4px solid #8f5d00;padding:10px 14px}\n"
        "</style></head><body>\n"
        "<h1>Laya 비교 (실험용)</h1>\n<p class=\"note\">검증되지 않은 실험 결과입니다. 1차 판정과 기존 결과물은 바뀌지 않았습니다. "
        f"Laya 점수 {summary['agree_at']} 이상을 '동의'로 봤고, 이 기준은 사람이 판정한 자료로 확인한 값이 아닙니다. "
        "'이견'인 후보(붉은 줄)는 비교용 사본에서만 표시를 뺐습니다. Laya는 1차 후보만 다시 봤으므로 "
        "1차가 놓친 내용은 여기서도 찾지 못합니다.</p>\n"
        f"<p>run {cell(run_id)} · 모델 {cell(adapter.model_version)} · 질문 {cell(adapter.question_version)}<br>\n"
        f"1차 후보 {summary['candidates']}개 중 동의 {summary['agree']}개, 이견 {summary['disagree']}개, "
        f"판단 못 함 {summary['not_assessed']}개 · 판단에 {summary['assess_seconds']}초</p>\n"
        "<table><thead><tr><th>문서</th><th>위치</th><th>1차 후보 문장</th><th>1차 점수</th><th>Laya 점수</th>"
        "<th>Laya</th><th>사람 판정</th></tr></thead>\n<tbody>\n" + "\n".join(body) + "\n</tbody></table>\n</body></html>\n"
    )


def run_comparison(database: Database, config: AppConfig, run_id: str, adapter: BatchAdapter, output_dir: Path,
                   progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    say = progress or (lambda message: None)
    run = load_run(database, run_id)
    if not run["classifier_version"]:
        raise ExportError(f"run {run_id}에는 1차 판정이 없습니다(UNTRAINED). 비교할 후보가 없습니다.")
    candidates = _candidates(run)
    say(f"1차 후보 {len(candidates)}개를 Laya로 다시 판단합니다...")
    started = time.perf_counter()
    assessments = adapter.assess_batch([
        (item["segment"]["normalized_text"], item["paragraph"]["section_title"] or "") for item in candidates
    ])
    elapsed = time.perf_counter() - started
    if len(assessments) != len(candidates):
        raise ExportError(f"2단계 판단 수({len(assessments)})가 후보 수({len(candidates)})와 다릅니다.")

    now = utc_now()
    calibration = f"none:agree_at={adapter.agree_at}"  # 보정하지 않은 원점수와, 동의로 본 기준
    with database.transaction() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO second_stage_results (result_id, prediction_id, model_revision, question_version, "
            "calibration_version, raw_scores_json, decision, status, sampling_reason, inclusion_probability, "
            "latency_ms, error_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (_result_id(item["segment"]["prediction_id"], adapter.model_version, adapter.question_version),
                 item["segment"]["prediction_id"], adapter.model_version, adapter.question_version, calibration,
                 json.dumps(assessment.raw_scores, ensure_ascii=False), assessment.decision, assessment.status,
                 SAMPLING_REASON, 1.0, assessment.latency_ms, assessment.error_code, now)
                for item, assessment in zip(candidates, assessments)
            ],
        )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item, assessment in zip(candidates, assessments):
        segment, paragraph = item["segment"], item["paragraph"]
        rows.append({
            "run_id": run_id, "file_name": item["document"]["file_name"], "location": paragraph["location"],
            "segment_id": segment["segment_id"], "prediction_id": segment["prediction_id"],
            "text": target_text(paragraph, segment), "stage1_score": segment["stage1_score"],
            "laya_status": assessment.status, "laya_score": _score(assessment), "laya_decision": assessment.decision,
            "verdict": _verdict(assessment), "human_label": segment["human_label"],
            "model_version": adapter.model_version, "question_version": adapter.question_version,
            "agree_at": adapter.agree_at,
        })
    atomic_write_text(output_dir / "laya_results.jsonl",
                      "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    summary = {
        "run_id": run_id, "model_version": adapter.model_version, "question_version": adapter.question_version,
        "agree_at": adapter.agree_at, "candidates": len(candidates),
        "agree": sum(a.status == "ok" and a.decision == "AGREE" for a in assessments),
        "disagree": sum(a.status == "ok" and a.decision == "DISAGREE" for a in assessments),
        "not_assessed": sum(a.status != "ok" for a in assessments),
        "assess_seconds": round(elapsed, 2),
        "ms_per_candidate": round(elapsed * 1000 / len(candidates), 1) if candidates else None,
    }
    atomic_write_text(output_dir / "laya_report.html", _report(run_id, adapter, rows, summary))

    # 비교용 사본: Laya가 정상적으로 '아니다'라고 본 1차 후보의 표시만 뺀다.
    # 판단하지 못한 후보와 사람이 YES로 판정한 구간은 그대로 둔다 (스펙 8.3).
    disagreed = {item["segment"]["segment_id"] for item, a in zip(candidates, assessments)
                 if a.status == "ok" and a.decision == "DISAGREE"}
    for document in run["documents"]:
        for paragraph in document["paragraphs"]:
            for segment in paragraph["segments"]:
                if segment["segment_id"] in disagreed and segment["human_label"] != "YES":
                    segment["marked"] = False
            paragraph["marked_spans"] = merge_spans(
                [span for segment in paragraph["segments"] if segment["marked"] for span in segment["spans"]]
            )
    summary["copies"] = annotate_documents(run, output_dir / "marked_laya_agree", config.export.pptx_summary_slide)
    summary["output_dir"] = str(output_dir)
    atomic_write_text(output_dir / "laya_summary.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary
