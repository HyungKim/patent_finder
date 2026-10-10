"""논리 문단을 모델 입력 길이에 맞는 segment로 나눈다 (스펙 5.2).

- 문장 경계로 먼저 나누고, 한 문장이 너무 길면 tokenizer offset으로 나누며 overlap을 둔다.
- 모든 segment는 원문 구간을 가진다. 상한을 넘는 입력을 조용히 자르지 않고 예외를 낸다.
- segmentation.unit=fine 이면 길이와 상관없이 문장마다, 표는 셀마다 따로 segment로 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import SegmentationConfig
from .normalization import normalize, normalize_text
from .paragraphs import LogicalParagraph
from .sentences import split_sentences
from .tokens import TokenCounter

_ROW_LABEL_TOKENS = 16
_SAFETY_MARGIN = 2  # 접두사와 본문을 이어 토큰화할 때 생길 수 있는 차이


class SegmentationError(RuntimeError):
    """대상 본문을 입력 상한 안으로 나눌 수 없음."""


@dataclass
class SegmentDraft:
    spans: list[tuple[int, int]]  # 문단 original_text 기준 원문 구간
    text: str  # 모델에 넣는 정규화 대상 텍스트
    token_count: int
    flags: list[str] = field(default_factory=list)


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] == " ":
        start += 1
    while end > start and text[end - 1] == " ":
        end -= 1
    return start, end


def _segment_text(original: str, base: int, hard_breaks: set[int], counter: TokenCounter,
                  config: SegmentationConfig, prefix: str = "", extra_flags: tuple[str, ...] = ()) -> list[SegmentDraft]:
    norm, omap = normalize(original)
    if not norm:
        return []
    forced = {
        index for index, ch in enumerate(norm)
        if ch == " " and any(base + pos in hard_breaks for pos in range(omap.starts[index], omap.ends[index]))
    }
    budget = config.target_tokens - (counter.count(prefix) if prefix else 0)
    if budget < 8:
        raise SegmentationError("표 머리글이 너무 길어 대상 본문을 넣을 수 없습니다.")
    drafts: list[SegmentDraft] = []

    def emit(start: int, end: int, flags: tuple[str, ...] = ()) -> None:
        start, end = _trim(norm, start, end)
        if end <= start:
            return
        o_start, o_end = omap.to_original(start, end)
        text = prefix + norm[start:end]
        drafts.append(SegmentDraft(
            spans=[(base + o_start, base + o_end)], text=text, token_count=counter.count(text),
            flags=list(extra_flags + flags),
        ))

    def windows(start: int, end: int) -> None:
        """한 문장이 예산을 넘을 때: 토큰 경계로 나누고 overlap을 둔다."""
        offsets = counter.offsets(norm[start:end])
        step = max(1, budget - config.overlap_tokens)
        cursor = 0
        while cursor < len(offsets):
            stop = min(cursor + budget, len(offsets))
            flags = ("token_split",) + (("overlap",) if cursor and config.overlap_tokens else ())
            emit(start + offsets[cursor][0], start + offsets[stop - 1][1], flags)
            if stop == len(offsets):
                break
            cursor += step

    fine = config.unit == "fine"
    current: tuple[int, int] | None = None
    for start, end in split_sentences(norm, forced):
        if counter.count(norm[start:end]) > budget:
            if current:
                emit(*current)
                current = None
            windows(start, end)
        elif fine:  # 문장마다 따로. 문장부호나 줄바꿈이 없는 개조식 글은 한 덩어리로 남는다
            emit(start, end)
        elif current is None:
            current = (start, end)
        elif counter.count(norm[current[0]:end]) <= budget:
            current = (current[0], end)
        else:
            emit(*current)
            current = (start, end)
    if current:
        emit(*current)
    return drafts


def _segment_row(paragraph: LogicalParagraph, counter: TokenCounter, config: SegmentationConfig) -> list[SegmentDraft]:
    """표 행: '열 제목: 셀 내용'을 이어 판정 입력을 만들고 셀의 원문 위치를 따로 보존한다."""
    pieces = []
    for cell in paragraph.cells or []:
        original = paragraph.original_text[cell["start"]:cell["end"]]
        norm, omap = normalize(original)
        if not norm:
            continue
        header = normalize_text(cell.get("header") or "")
        label = f"{header}: " if header and header != norm else ""
        o_start, o_end = omap.to_original(0, len(norm))
        pieces.append({
            "text": label + norm, "label": label, "norm": norm,
            "span": (cell["start"] + o_start, cell["start"] + o_end), "cell": cell,
        })
    if not pieces:
        return []
    if config.unit == "fine":  # 셀마다 따로 ('열 제목: 셀 내용'). 셀 안의 문장도 따로 나뉜다
        drafts = []
        for piece in pieces:
            cell = piece["cell"]
            drafts.extend(_segment_text(
                paragraph.original_text[cell["start"]:cell["end"]], cell["start"], paragraph.hard_breaks,
                counter, config, prefix=piece["label"], extra_flags=("cell",),
            ))
        return drafts
    full = " | ".join(piece["text"] for piece in pieces)
    total = counter.count(full)
    if total <= config.target_tokens:
        return [SegmentDraft(spans=[piece["span"] for piece in pieces], text=full, token_count=total)]

    # 행이 너무 길면 셀 단위로 나눈다. 행 제목(첫 셀)이 짧으면 각 조각 앞에 붙인다.
    row_label = ""
    rest = pieces
    if len(pieces) > 1 and counter.count(pieces[0]["norm"]) <= _ROW_LABEL_TOKENS:
        row_label = pieces[0]["norm"] + " | "
        rest = pieces[1:]
    drafts: list[SegmentDraft] = []
    group: list[dict] = []

    def flush() -> None:
        if not group:
            return
        text = row_label + " | ".join(piece["text"] for piece in group)
        spans = ([pieces[0]["span"]] if row_label else []) + [piece["span"] for piece in group]
        drafts.append(SegmentDraft(spans=spans, text=text, token_count=counter.count(text), flags=["row_split"]))
        group.clear()

    for piece in rest:
        candidate = row_label + " | ".join(p["text"] for p in group + [piece])
        if counter.count(candidate) <= config.target_tokens:
            group.append(piece)
            continue
        flush()
        if counter.count(row_label + piece["text"]) <= config.target_tokens:
            group.append(piece)
            continue
        cell = piece["cell"]
        drafts.extend(_segment_text(
            paragraph.original_text[cell["start"]:cell["end"]], cell["start"], paragraph.hard_breaks,
            counter, config, prefix=row_label + piece["label"], extra_flags=("row_split",),
        ))
    flush()
    return drafts


def segment_paragraph(paragraph: LogicalParagraph, counter: TokenCounter,
                      config: SegmentationConfig) -> list[SegmentDraft]:
    if paragraph.cells:
        drafts = _segment_row(paragraph, counter, config)
    else:
        drafts = _segment_text(paragraph.original_text, 0, paragraph.hard_breaks, counter, config)
    limit = config.max_input_tokens - counter.overhead - _SAFETY_MARGIN
    for draft in drafts:
        if draft.token_count > limit:
            raise SegmentationError(
                f"segment 길이({draft.token_count} 토큰)가 입력 상한({limit})을 넘습니다. 조용히 자르지 않습니다."
            )
    return drafts
