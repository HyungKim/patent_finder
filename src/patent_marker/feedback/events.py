"""피드백 이벤트 기록 (스펙 9절, 10절).

사용자 판단은 즉시 DB에 이벤트로 남는다. 수정은 덮어쓰지 않고 새 이벤트 + supersedes로 남긴다.
운영 모델은 피드백 한 건으로 바뀌지 않는다.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from ..runtime import utc_now
from ..storage import Database
from .resolution import IMPLICIT_SOURCE, LABELS, REASON_CODES, resolve_target

SOURCES = ("review", "manual_add", "page_review", IMPLICIT_SOURCE, "adjudication")


class FeedbackError(ValueError):
    pass


def _latest_by_reviewer(connection: Any, target_type: str, target_id: str, reviewer_id: str) -> Any:
    column = "segment_id" if target_type == "segment" else "paragraph_id"
    return connection.execute(
        f"SELECT * FROM feedback_events WHERE target_type = ? AND {column} = ? AND reviewer_id = ? "
        "AND is_adjudication = 0 ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (target_type, target_id, reviewer_id),
    ).fetchone()


def _insert(connection: Any, *, target_type: str, target_id: str, label: str, reviewer_id: str,
            guideline_version: str, reason_codes: list[str], comment: str | None, prediction_id: str | None,
            source: str, batch_id: str | None, is_adjudication: bool) -> dict[str, Any]:
    if target_type == "segment":
        target = connection.execute("SELECT segment_id, input_hash FROM segments WHERE segment_id = ?",
                                    (target_id,)).fetchone()
        context_hash = target["input_hash"] if target else None
    else:
        target = connection.execute("SELECT paragraph_id, text_hash FROM paragraphs WHERE paragraph_id = ?",
                                    (target_id,)).fetchone()
        context_hash = target["text_hash"] if target else None
    if target is None:
        raise FeedbackError(f"존재하지 않는 대상입니다: {target_type} {target_id}")
    previous = None if is_adjudication else _latest_by_reviewer(connection, target_type, target_id, reviewer_id)
    feedback_id = "fb-" + uuid.uuid4().hex
    connection.execute(
        "INSERT INTO feedback_events (feedback_id, target_type, segment_id, paragraph_id, prediction_id, label, "
        "reason_codes_json, comment, reviewer_id, created_at, supersedes_feedback_id, review_context_hash, source, "
        "batch_id, is_adjudication) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (feedback_id, target_type, target_id if target_type == "segment" else None,
         target_id if target_type == "paragraph" else None, prediction_id, label,
         json.dumps(reason_codes), comment or None, reviewer_id, utc_now(),
         previous["feedback_id"] if previous else None, context_hash, source, batch_id, int(is_adjudication)),
    )
    resolved = resolve_target(connection, target_type, target_id, guideline_version)
    return {"feedback_id": feedback_id, "supersedes": previous["feedback_id"] if previous else None,
            "resolved": resolved}


def record_feedback(database: Database, *, target_type: str, target_id: str, label: str, reviewer_id: str,
                    guideline_version: str, reason_codes: list[str] | None = None, comment: str | None = None,
                    prediction_id: str | None = None, source: str = "review", batch_id: str | None = None,
                    is_adjudication: bool = False) -> dict[str, Any]:
    """YES/NO/HOLD 판단 한 건을 기록하고 확정 라벨을 갱신한다."""
    if target_type not in ("segment", "paragraph"):
        raise FeedbackError("target_type은 segment 또는 paragraph여야 합니다.")
    if label not in LABELS:
        raise FeedbackError(f"label은 {LABELS} 중 하나여야 합니다.")
    reason_codes = list(reason_codes or [])
    unknown = sorted(set(reason_codes) - set(REASON_CODES))
    if unknown:
        raise FeedbackError(f"알 수 없는 사유 코드: {', '.join(unknown)}")
    if source not in SOURCES:
        raise FeedbackError(f"알 수 없는 source: {source}")
    if not reviewer_id:
        raise FeedbackError("reviewer_id가 필요합니다.")
    with database.transaction() as connection:
        if target_type == "segment" and source == "review" and label == "YES":
            # 시스템이 후보로 표시하지 않은 구간을 사람이 YES로 올린 경우: 수동 발견 후보
            latest = connection.execute(
                "SELECT final_decision FROM latest_predictions WHERE segment_id = ?", (target_id,)
            ).fetchone()
            if latest is None or latest["final_decision"] != "CANDIDATE":
                source = "manual_add"
        return _insert(connection, target_type=target_type, target_id=target_id, label=label,
                       reviewer_id=reviewer_id, guideline_version=guideline_version, reason_codes=reason_codes,
                       comment=comment, prediction_id=prediction_id, source=source, batch_id=batch_id,
                       is_adjudication=is_adjudication)


def group_segments(database: Database, document_id: str, unit: int | None, section_id: str | None) -> list[Any]:
    """검토 단위(슬라이드/페이지, 없으면 섹션)에 속한 segment들."""
    if unit is not None:
        condition, value = "p.unit = ?", unit
    elif section_id is not None:
        condition, value = "p.section_id = ?", section_id
    else:
        condition, value = "p.unit IS NULL AND p.section_id IS ?", None
    return database.query(
        f"SELECT s.segment_id FROM segments s JOIN paragraphs p ON p.paragraph_id = s.paragraph_id "
        f"WHERE s.document_id = ? AND {condition} ORDER BY s.seq", (document_id, value),
    )


def record_page_review(database: Database, *, document_id: str, unit: int | None = None,
                       section_id: str | None = None, yes_segment_ids: list[str],
                       hold_segment_ids: list[str] | None = None, reviewer_id: str, guideline_version: str,
                       predictions: dict[str, str] | None = None) -> dict[str, Any]:
    """슬라이드/페이지 단위 일괄 검토 (변경안 A5).

    선택한 segment는 YES, 보류로 표시한 segment는 HOLD, 나머지는 '묵시적 NO'로 기록한다.
    이미 같은 라벨을 명시적으로 준 segment는 다시 기록하지 않는다.
    """
    hold_segment_ids = hold_segment_ids or []
    predictions = predictions or {}
    members = [row["segment_id"] for row in group_segments(database, document_id, unit, section_id)]
    if not members:
        raise FeedbackError("검토 단위에 segment가 없습니다.")
    unknown = (set(yes_segment_ids) | set(hold_segment_ids)) - set(members)
    if unknown:
        raise FeedbackError(f"검토 단위에 속하지 않은 segment: {sorted(unknown)[:3]}")
    batch_id = "batch-" + uuid.uuid4().hex[:16]
    counts = {"YES": 0, "HOLD": 0, "NO": 0, "unchanged": 0}
    with database.transaction() as connection:
        for segment_id in members:
            if segment_id in yes_segment_ids:
                label, source = "YES", "page_review"
            elif segment_id in hold_segment_ids:
                label, source = "HOLD", "page_review"
            else:
                label, source = "NO", IMPLICIT_SOURCE
            previous = _latest_by_reviewer(connection, "segment", segment_id, reviewer_id)
            if previous is not None and previous["label"] == label:
                counts["unchanged"] += 1
                continue
            _insert(connection, target_type="segment", target_id=segment_id, label=label,
                    reviewer_id=reviewer_id, guideline_version=guideline_version, reason_codes=[], comment=None,
                    prediction_id=predictions.get(segment_id), source=source, batch_id=batch_id,
                    is_adjudication=False)
            counts[label] += 1
    return {"batch_id": batch_id, "segments": len(members), "recorded": counts}


def feedback_history(database: Database, target_type: str, target_id: str) -> list[dict[str, Any]]:
    column = "segment_id" if target_type == "segment" else "paragraph_id"
    rows = database.query(
        f"SELECT * FROM feedback_events WHERE target_type = ? AND {column} = ? ORDER BY created_at, rowid",
        (target_type, target_id),
    )
    history = []
    for row in rows:
        item = dict(row)
        item["reason_codes"] = json.loads(item.pop("reason_codes_json"))
        history.append(item)
    return history


def register_unparsed_positive(database: Database, *, document_id: str, unit: int | None, note: str,
                               reviewer_id: str) -> str:
    """파서가 읽지 못한 후보(이미지 안의 내용 등)를 등록한다. end-to-end 평가에서 FN으로 센다."""
    if not note.strip():
        raise FeedbackError("누락 후보의 설명(note)이 필요합니다.")
    if database.query_one("SELECT 1 FROM documents WHERE document_id = ?", (document_id,)) is None:
        raise FeedbackError(f"존재하지 않는 문서입니다: {document_id}")
    item_id = "miss-" + uuid.uuid4().hex[:16]
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO unparsed_positives (id, document_id, unit, note, reviewer_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (item_id, document_id, unit, note.strip(), reviewer_id, utc_now()),
        )
    return item_id
