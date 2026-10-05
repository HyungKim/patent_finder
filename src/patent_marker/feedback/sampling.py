"""검토 큐와 cold start용 규칙 제안 (스펙 7.2, 13절).

규칙 제안은 라벨링 순서를 정하는 힌트일 뿐이며 정답으로 저장하지 않고,
시스템 판정(final_decision)으로 표시하지도 않는다.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..storage import Database

FILTERS = ("candidates", "all", "unreviewed", "hold", "noncandidate_sample", "rule_suggest")

_RULES: list[tuple[str, float, re.Pattern[str]]] = [
    ("CONDITION_ACTION", 2.0, re.compile(
        r"(으?면|경우|때|시)\s*[^.]{0,40}(한다|함|하고|시킨|시킴|전환|적용|조정|조절|보정|제한|차단|대체|선택)|\b(if|when|whenever)\b.{0,80}\b(then|is|are)\b",
        re.IGNORECASE)),
    ("NUMERIC_CONDITION", 1.5, re.compile(
        r"(\d+(\.\d+)?\s*(%|도|ms|초|mV|V|A|Hz|kHz|mm|µm|nm|ppm|배|프레임|단계)?\s*(이상|이하|초과|미만|넘으면|보다))|[<>≤≥]\s*\d")),
    ("METHOD_VERB", 1.0, re.compile(
        r"(제어|조절|조정|보정|보상|산출|계산|추정|판정|검출|분류|전환|변환|갱신|필터|가중|융합|정합|매칭|양자화|보간|평활|샘플링|스케줄|할당)")),
    ("STRUCTURE_TERM", 0.8, re.compile(
        r"(구조|구성|배치|회로|모듈|알고리즘|임계값|threshold|파라미터|weight|가중치|테이블|맵|프로파일|시퀀스)",
        re.IGNORECASE)),
    ("IMPROVEMENT_CLAIM", 0.5, re.compile(r"(제안|신규|개선|기존 대비|새로운|proposed|novel)", re.IGNORECASE)),
    ("ADMIN_CUE", -2.0, re.compile(
        r"(일정|예산|회의|인력|담당|출장|품의|예정|계획|분기|보고서|교육|감사합니다|목차|schedule|budget|meeting)",
        re.IGNORECASE)),
]


def rule_hints(text: str) -> tuple[float, list[str]]:
    """(점수, 걸린 규칙 이름). 점수가 높을수록 먼저 검토하도록 제안한다."""
    score = 0.0
    tags: list[str] = []
    for name, weight, pattern in _RULES:
        if pattern.search(text):
            score += weight
            tags.append(name)
    return score, tags


def _stable_fraction(key: str) -> float:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


_BASE_QUERY = """
SELECT s.segment_id, s.paragraph_id, s.document_id, s.seq, s.segment_index, s.normalized_text,
       s.target_original_spans_json, s.context_segment_ids_json, s.quality_flags AS segment_flags,
       p.kind, p.unit, p.section_id, p.section_title, p.original_text, p.quality_flags AS paragraph_flags,
       d.file_name, d.format, d.parse_status,
       lp.prediction_id, lp.stage1_score, lp.final_decision, lp.threshold, lp.classifier_version, lp.run_id,
       rl.label AS human_label, rl.adjudication_status, rl.implicit AS label_implicit
FROM segments s
JOIN paragraphs p ON p.paragraph_id = s.paragraph_id
JOIN documents d ON d.document_id = s.document_id
LEFT JOIN latest_predictions lp ON lp.segment_id = s.segment_id
LEFT JOIN resolved_labels rl ON rl.target_type = 'segment' AND rl.target_id = s.segment_id
WHERE d.is_current = 1
"""


def row_to_item(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["target_original_spans"] = json.loads(item.pop("target_original_spans_json"))
    item["context_segment_ids"] = json.loads(item.pop("context_segment_ids_json"))
    item["quality_flags"] = sorted(set(json.loads(item.pop("segment_flags")) + json.loads(item.pop("paragraph_flags"))))
    score, tags = rule_hints(item["normalized_text"])
    item["rule_hint_score"] = score
    item["rule_hints"] = tags
    return item


def fetch_segments(database: Database, document_id: str | None = None,
                   segment_ids: list[str] | None = None) -> list[dict[str, Any]]:
    sql, params = _BASE_QUERY, []
    if document_id:
        sql += " AND s.document_id = ?"
        params.append(document_id)
    if segment_ids is not None:
        if not segment_ids:
            return []
        sql += f" AND s.segment_id IN ({','.join('?' * len(segment_ids))})"
        params.extend(segment_ids)
    sql += " ORDER BY d.file_name, s.document_id, s.seq"
    return [row_to_item(row) for row in database.query(sql, params)]


def fetch_queue(database: Database, queue_filter: str = "candidates", document_id: str | None = None,
                limit: int = 50, offset: int = 0, sample_rate: float = 0.1) -> dict[str, Any]:
    """검토 큐. 후보만/전체/미검토/보류/비후보 표본/규칙 제안을 전환할 수 있다."""
    if queue_filter not in FILTERS:
        raise ValueError(f"filter는 {FILTERS} 중 하나여야 합니다.")
    items = fetch_segments(database, document_id=document_id)
    if queue_filter == "candidates":
        items = [i for i in items if i["final_decision"] == "CANDIDATE"]
        items.sort(key=lambda i: -(i["stage1_score"] or 0.0))
    elif queue_filter == "unreviewed":
        items = [i for i in items if i["human_label"] is None]
    elif queue_filter == "hold":
        items = [i for i in items if i["human_label"] == "HOLD"]
    elif queue_filter == "noncandidate_sample":
        # 후보로 표시되지 않은 미검토 구간의 고정 표본: false negative와 선택 편향 확인용
        items = [i for i in items if i["final_decision"] != "CANDIDATE" and i["human_label"] is None
                 and _stable_fraction(i["segment_id"]) < sample_rate]
        items.sort(key=lambda i: _stable_fraction(i["segment_id"]))
    elif queue_filter == "rule_suggest":
        # cold start: 규칙 점수 상위와 무작위 표본을 번갈아 섞는다.
        pending = [i for i in items if i["human_label"] is None]
        ranked = sorted(pending, key=lambda i: -i["rule_hint_score"])
        randomized = sorted(pending, key=lambda i: _stable_fraction(i["segment_id"]))
        seen: set[str] = set()
        mixed: list[dict[str, Any]] = []
        for a, b in zip(ranked, randomized):
            for candidate, reason in ((a, "rule"), (b, "random")):
                if candidate["segment_id"] not in seen:
                    seen.add(candidate["segment_id"])
                    mixed.append({**candidate, "queue_reason": reason})
        items = mixed
    total = len(items)
    return {"filter": queue_filter, "total": total, "offset": offset, "items": items[offset: offset + limit]}
