"""원시 블록을 논리 문단으로 만든다 (스펙 5.2).

짧은 불릿은 구조적으로 연결될 때만 묶는다:
  1) 상위 항목과 바로 뒤따르는 하위 수준 항목 (같은 텍스트 상자/목록)
  2) 콜론으로 끝나는 도입 문장과 바로 뒤따르는 목록 항목
같은 수준의 이웃 불릿은 묶지 않고 문맥으로만 참조한다.
segmentation.unit=fine 이면 아무것도 묶지 않는다 (불릿마다 따로).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import SegmentationConfig
from ..parsers.base import Block
from .normalization import normalize_text
from .tokens import TokenCounter

HEADING_KINDS = ("title", "heading")
_MAX_GROUP_ITEMS = 8


@dataclass
class LogicalParagraph:
    kind: str
    original_text: str
    # 원문 조각별 위치: [{"start", "end", "kind", "level", "locator"}]
    parts: list[dict[str, Any]]
    unit: int | None = None
    section_key: str | None = None
    section_title: str | None = None
    flags: list[str] = field(default_factory=list)
    cells: list[dict[str, Any]] | None = None
    # original_text 안에서 문장 경계로 취급할 줄바꿈 문자의 위치
    hard_breaks: set[int] = field(default_factory=set)

    @property
    def is_heading(self) -> bool:
        return self.kind in HEADING_KINDS


def _hard_positions(block: Block) -> set[int]:
    if not block.hard_newlines:
        return set()
    return {index for index, ch in enumerate(block.text) if ch in "\n\r\v"}


def _single(block: Block) -> LogicalParagraph:
    return LogicalParagraph(
        kind=block.kind, original_text=block.text,
        parts=[{"start": 0, "end": len(block.text), "kind": block.kind, "level": block.level,
                "locator": block.locator}],
        unit=block.unit, section_key=block.section_key, section_title=block.section_title,
        flags=list(block.flags), cells=block.cells, hard_breaks=_hard_positions(block),
    )


def _merge(blocks: list[Block]) -> LogicalParagraph:
    pieces: list[str] = []
    parts: list[dict[str, Any]] = []
    hard: set[int] = set()
    flags: list[str] = []
    position = 0
    for index, block in enumerate(blocks):
        if index:
            hard.add(position)
            pieces.append("\n")
            position += 1
        parts.append({"start": position, "end": position + len(block.text), "kind": block.kind,
                      "level": block.level, "locator": block.locator})
        hard.update(position + offset for offset in _hard_positions(block))
        pieces.append(block.text)
        position += len(block.text)
        for flag in block.flags:
            if flag not in flags:
                flags.append(flag)
    first = blocks[0]
    return LogicalParagraph(
        kind="list_group", original_text="".join(pieces), parts=parts, unit=first.unit,
        section_key=first.section_key, section_title=first.section_title, flags=flags, hard_breaks=hard,
    )


def _children(blocks: list[Block], index: int) -> list[int]:
    """index의 블록에 구조적으로 딸린 뒤따르는 항목들의 위치."""
    parent = blocks[index]
    result: list[int] = []
    lead_in = parent.kind in ("paragraph", "list_item") and parent.text.rstrip().endswith((":", "："))
    container = None
    for cursor in range(index + 1, len(blocks)):
        block = blocks[cursor]
        if block.kind != "list_item" or block.section_key != parent.section_key or block.unit != parent.unit:
            break
        if parent.kind == "list_item" and block.container == parent.container and block.level > parent.level:
            result.append(cursor)
            continue
        if lead_in and (parent.kind == "paragraph" or block.container == parent.container):
            container = block.container if container is None else container
            if block.container == container and block.level >= (parent.level if parent.kind == "list_item" else 0):
                result.append(cursor)
                continue
        break
    return result


def build_paragraphs(blocks: list[Block], counter: TokenCounter, config: SegmentationConfig) -> list[LogicalParagraph]:
    paragraphs: list[LogicalParagraph] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        group = [block]
        if block.kind in ("list_item", "paragraph") and not block.cells and config.unit != "fine":
            total = counter.count(normalize_text(block.text))
            if total <= config.short_item_tokens:
                for cursor in _children(blocks, index):
                    size = counter.count(normalize_text(blocks[cursor].text))
                    if (size > config.short_item_tokens or total + size + 1 > config.target_tokens
                            or len(group) >= _MAX_GROUP_ITEMS):
                        break
                    group.append(blocks[cursor])
                    total += size + 1
        paragraphs.append(_merge(group) if len(group) > 1 else _single(block))
        index += len(group)
    return paragraphs
