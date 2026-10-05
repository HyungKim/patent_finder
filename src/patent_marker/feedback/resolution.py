"""피드백 이벤트에서 확정 라벨을 만든다 (스펙 9.2).

- 같은 검토자의 수정 이력은 최신 이벤트가 유효하다.
- 검토자 간 충돌은 최신 작성자 우선으로 풀지 않고 HOLD(합의 대기)로 둔다. 합의 이벤트가 있으면 그것을 따른다.
- 파싱·위치 오류 사유가 붙은 대상과 문단 수준 라벨은 이진 학습에서 제외한다.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..runtime import utc_now

REASON_CODES = (
    "CONCRETE_METHOD", "GOAL_ONLY", "ADMIN_CONTENT", "INSUFFICIENT_CONTEXT",
    "PARSING_ERROR", "WRONG_SPAN", "DOMAIN_REVIEW_NEEDED", "OTHER",
)
TRAINING_EXCLUDED_CODES = {"PARSING_ERROR", "WRONG_SPAN"}
LABELS = ("YES", "NO", "HOLD")
IMPLICIT_SOURCE = "page_review_implicit"


def effective_events(events: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], sqlite3.Row | None]:
    """(검토자별 최신 이벤트, 최신 합의 이벤트)."""
    latest: dict[str, sqlite3.Row] = {}
    adjudication = None
    for event in events:  # created_at, rowid 순
        if event["is_adjudication"]:
            adjudication = event
        else:
            latest[event["reviewer_id"]] = event
    return list(latest.values()), adjudication


def resolve_target(connection: sqlite3.Connection, target_type: str, target_id: str,
                   guideline_version: str) -> dict[str, Any] | None:
    """대상의 확정 라벨을 다시 계산해 resolved_labels에 반영한다. transaction 안에서 호출한다."""
    column = "segment_id" if target_type == "segment" else "paragraph_id"
    events = connection.execute(
        f"SELECT rowid, * FROM feedback_events WHERE target_type = ? AND {column} = ? ORDER BY created_at, rowid",
        (target_type, target_id),
    ).fetchall()
    if not events:
        connection.execute("DELETE FROM resolved_labels WHERE target_type = ? AND target_id = ?",
                           (target_type, target_id))
        return None
    per_reviewer, adjudication = effective_events(events)
    if adjudication is not None:
        label, status, used = adjudication["label"], "ADJUDICATED", [adjudication]
    else:
        labels = {event["label"] for event in per_reviewer}
        used = per_reviewer
        if len(labels) > 1:
            label, status = "HOLD", "CONFLICT_PENDING"
        else:
            label = next(iter(labels))
            status = "SINGLE_REVIEWER" if len(per_reviewer) == 1 else "AGREED"
    codes = {code for event in used for code in json.loads(event["reason_codes_json"])}
    trainable = (
        target_type == "segment" and label in ("YES", "NO") and status != "CONFLICT_PENDING"
        and not (codes & TRAINING_EXCLUDED_CODES)
    )
    implicit = all(event["source"] == IMPLICIT_SOURCE for event in used)
    resolved = {
        "target_type": target_type, "target_id": target_id, "label": label, "adjudication_status": status,
        "source_feedback_ids": [event["feedback_id"] for event in used],
        "trainable": bool(trainable), "implicit": bool(implicit), "reason_codes": sorted(codes),
    }
    connection.execute(
        "INSERT INTO resolved_labels (target_type, target_id, label, adjudication_status, source_feedback_ids_json, "
        "label_guideline_version, resolved_at, trainable, implicit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (target_type, target_id) DO UPDATE SET label = excluded.label, "
        "adjudication_status = excluded.adjudication_status, "
        "source_feedback_ids_json = excluded.source_feedback_ids_json, "
        "label_guideline_version = excluded.label_guideline_version, resolved_at = excluded.resolved_at, "
        "trainable = excluded.trainable, implicit = excluded.implicit",
        (target_type, target_id, label, status, json.dumps(resolved["source_feedback_ids"]),
         guideline_version, utc_now(), int(trainable), int(implicit)),
    )
    return resolved
