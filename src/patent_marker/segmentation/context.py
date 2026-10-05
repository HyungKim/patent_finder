"""문맥 구성. 이전/다음 segment와 제목은 같은 섹션 안에서 문맥으로만 쓴다 (스펙 5.2).

마킹과 라벨은 항상 판정 대상에 귀속한다.
- features.mode=separate: 대상과 문맥을 따로 임베딩하므로 여기서는 참조 관계만 정한다.
- features.mode=composed: 스펙 5.2 원안대로 한 문자열로 묶는다 (compose_input).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import SegmentationConfig
from ..runtime import canonical_json, sha256_text
from .tokens import TokenCounter, head, tail


@dataclass
class ContextRef:
    prev: int | None  # 문서 내 segment 순번
    next: int | None
    title: int | None


def assign_context(section_keys: list[str | None], is_heading: list[bool]) -> list[ContextRef]:
    """문서 순서로 나열된 segment들에 대해 같은 섹션 안의 이전/다음/제목 segment를 정한다."""
    count = len(section_keys)
    title_of: dict[str | None, int] = {}
    for index in range(count):
        if is_heading[index] and section_keys[index] not in title_of:
            title_of[section_keys[index]] = index
    refs: list[ContextRef] = []
    for index in range(count):
        key = section_keys[index]
        prev = index - 1 if index > 0 and section_keys[index - 1] == key else None
        nxt = index + 1 if index + 1 < count and section_keys[index + 1] == key else None
        title = title_of.get(key)
        if title == index:
            title = None
        # 제목이 곧 이웃이면 중복해서 세지 않는다.
        if title is not None and title in (prev, nxt):
            title = None
        refs.append(ContextRef(prev=prev, next=nxt, title=title))
    return refs


def input_hash(target: str, title: str | None, prev: str | None, nxt: str | None) -> str:
    """검토자에게 보여주고 모델에 넣은 '대상 + 문맥'의 해시."""
    return sha256_text(canonical_json({"target": target, "title": title, "prev": prev, "next": nxt}))


def compose_input(target: str, title: str | None, prev: str | None, nxt: str | None,
                  counter: TokenCounter, config: SegmentationConfig) -> str:
    """스펙 5.2의 결합 입력. 상한을 넘으면 문맥부터 줄이고, 대상은 자르지 않는다."""
    limit = config.max_input_tokens - counter.overhead - 2
    title_text = head(title, counter, config.title_tokens) if title else ""
    budget = config.context_tokens
    while True:
        if prev and nxt:
            prev_budget, next_budget = budget // 2, budget - budget // 2
        else:
            prev_budget = next_budget = budget
        lines: list[str] = []
        if title_text:
            lines.append(f"[제목] {title_text}")
        prev_text = tail(prev, counter, prev_budget) if prev else ""
        if prev_text:
            lines.append(f"[이전 문맥] {prev_text}")
        lines.append(f"[판정 대상] {target}")
        next_text = head(nxt, counter, next_budget) if nxt else ""
        if next_text:
            lines.append(f"[다음 문맥] {next_text}")
        composed = "\n".join(lines)
        if counter.count(composed) <= limit:
            return composed
        if budget > 0:
            budget = budget // 2
        elif title_text:
            title_text = ""
        else:
            raise ValueError("대상 본문이 입력 상한을 넘습니다. 대상은 분할기에서 다시 나눠야 합니다.")


def context_summary(ref: ContextRef, segment_ids: list[str]) -> dict[str, Any]:
    return {
        "prev": segment_ids[ref.prev] if ref.prev is not None else None,
        "next": segment_ids[ref.next] if ref.next is not None else None,
        "title": segment_ids[ref.title] if ref.title is not None else None,
    }
