"""내보내기 공통 데이터: 한 run의 문서·문단·segment·예측·사람 판정을 모은다.

시스템 판정(final_decision)과 사람 판정(human_label)은 항상 다른 필드로 둔다.
"""
from __future__ import annotations

import json
from typing import Any

from ..feedback.sampling import rule_hints
from ..policies.thresholds import get_policy
from ..segmentation.offsets import merge_spans
from ..storage import Database


class ExportError(RuntimeError):
    pass


def location_label(fmt: str, unit: int | None, parts: list[dict[str, Any]]) -> str:
    """사람이 원문에서 찾아갈 수 있는 위치 설명."""
    locator = parts[0]["locator"] if parts else {}
    if fmt == "pptx":
        label = f"슬라이드 {unit}"
        if locator.get("notes"):
            return label + " · 발표자 노트"
        if "table_row" in locator:
            return f"{label} · 표 {locator['table_row'] + 1}행"
        name = locator.get("shape_name")
        return f"{label} · {name}" if name else label
    if fmt == "pdf":
        if "table" in locator:
            return f"{unit}쪽 · 표 {locator['row'] + 1}행"
        return f"{unit}쪽"
    if fmt == "docx":
        if "row" in locator:
            return f"본문 요소 {locator.get('body_index', 0) + 1} · 표 {locator['row'] + 1}행"
        return f"본문 요소 {locator.get('body_index', 0) + 1}"
    if "line_start" in locator:
        last = parts[-1]["locator"].get("line_end", locator["line_start"])
        return f"{locator['line_start']}행" if last == locator["line_start"] else f"{locator['line_start']}–{last}행"
    return "위치 정보 없음"


def is_marked(segment: dict[str, Any]) -> bool:
    """사본·보고서에 표시할 구간: 시스템 후보(사람이 NO로 판정한 것 제외) 또는 사람이 YES로 판정한 구간."""
    if segment["human_label"] == "YES":
        return True
    return segment["final_decision"] == "CANDIDATE" and segment["human_label"] != "NO"


def load_run(database: Database, run_id: str) -> dict[str, Any]:
    run = database.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if run is None:
        raise ExportError(f"run을 찾을 수 없습니다: {run_id}")
    data: dict[str, Any] = dict(run)
    data["details"] = json.loads(data.pop("details_json"))
    data["policy"] = get_policy(database, data["policy_version"])
    documents = []
    for entry in database.query(
        "SELECT rd.input_path, rd.status AS run_status, rd.error AS run_error, d.* FROM run_documents rd "
        "LEFT JOIN documents d ON d.document_id = rd.document_id WHERE rd.run_id = ? ORDER BY rd.input_path",
        (run_id,),
    ):
        document = dict(entry)
        document["coverage"] = json.loads(document.pop("coverage_json") or "{}")
        document["warnings"] = json.loads(document.pop("warnings_json") or "[]")
        document["paragraphs"] = []
        if document["document_id"]:
            segments_by_paragraph: dict[str, list[dict[str, Any]]] = {}
            for row in database.query(
                "SELECT s.*, pr.prediction_id, pr.stage1_score, pr.stage1_decision, pr.final_decision, "
                "pr.threshold AS prediction_threshold, pr.classifier_version, pr.policy_version AS prediction_policy, "
                "pr.embedding_id, e.file_path AS embedding_file, e.row_index AS embedding_row, e.encoder_revision, "
                "rl.label AS human_label, rl.adjudication_status, rl.implicit AS label_implicit, "
                "rl.source_feedback_ids_json "
                "FROM segments s "
                "LEFT JOIN predictions pr ON pr.segment_id = s.segment_id AND pr.run_id = ? "
                "LEFT JOIN embeddings e ON e.embedding_id = pr.embedding_id "
                "LEFT JOIN resolved_labels rl ON rl.target_type = 'segment' AND rl.target_id = s.segment_id "
                "WHERE s.document_id = ? ORDER BY s.seq", (run_id, document["document_id"]),
            ):
                segment = dict(row)
                segment["spans"] = [tuple(span) for span in json.loads(segment.pop("target_original_spans_json"))]
                segment["context_segment_ids"] = json.loads(segment.pop("context_segment_ids_json"))
                segment["quality_flags"] = json.loads(segment["quality_flags"])
                segment["rule_hints"] = rule_hints(segment["normalized_text"])[1]
                segment["feedback"] = None
                if segment["human_label"]:
                    ids = json.loads(segment.pop("source_feedback_ids_json") or "[]")
                    events = database.query(
                        f"SELECT reviewer_id, reason_codes_json, comment, source FROM feedback_events "
                        f"WHERE feedback_id IN ({','.join('?' * len(ids))})", ids) if ids else []
                    segment["feedback"] = {
                        "label": segment["human_label"], "adjudication_status": segment["adjudication_status"],
                        "implicit": bool(segment["label_implicit"]),
                        "reviewer_ids": sorted({event["reviewer_id"] for event in events}),
                        "reason_codes": sorted({code for event in events for code in json.loads(event["reason_codes_json"])}),
                        "comments": [event["comment"] for event in events if event["comment"]],
                    }
                else:
                    segment.pop("source_feedback_ids_json", None)
                segment["marked"] = is_marked(segment)
                segments_by_paragraph.setdefault(segment["paragraph_id"], []).append(segment)
            for row in database.query(
                "SELECT * FROM paragraphs WHERE document_id = ? ORDER BY order_index", (document["document_id"],)
            ):
                paragraph = dict(row)
                paragraph["locator"] = json.loads(paragraph.pop("source_locator_json"))
                paragraph["quality_flags"] = json.loads(paragraph["quality_flags"])
                paragraph["segments"] = segments_by_paragraph.get(paragraph["paragraph_id"], [])
                paragraph["location"] = location_label(document["format"], paragraph["unit"],
                                                       paragraph["locator"].get("parts", []))
                # 중첩 segment의 중복 표시를 합친다.
                paragraph["marked_spans"] = merge_spans(
                    [span for segment in paragraph["segments"] if segment["marked"] for span in segment["spans"]]
                )
                scores = [s["stage1_score"] for s in paragraph["segments"] if s["stage1_score"] is not None]
                paragraph["score"] = max(scores) if scores else None
                document["paragraphs"].append(paragraph)
        documents.append(document)
    data["documents"] = documents
    return data


def target_text(paragraph: dict[str, Any], segment: dict[str, Any]) -> str:
    """segment의 원문 구간 텍스트. 표 행은 셀 구분자를 포함해 한 구간으로 보여준다."""
    spans = segment["spans"]
    if paragraph["kind"] == "table_row" and spans:
        return paragraph["original_text"][min(a for a, _ in spans):max(b for _, b in spans)]
    return " … ".join(paragraph["original_text"][start:end] for start, end in spans)
