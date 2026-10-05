"""TXT/MD 파서. 위치는 행 번호와 디코딩된 원문의 문자 구간으로 보존한다."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .base import Block, ParseResult, finalize_status, strip_list_marker

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_TERMINAL = tuple(".!?:;。！？…)]」』”\"'")


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _split_md_row(line: str) -> list[tuple[int, int]]:
    """마크다운 표 행에서 각 셀의 (start, end) 구간(행 내 offset)을 구한다."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, ch in enumerate(line):
        if ch == "|" and (index == 0 or line[index - 1] != "\\"):
            spans.append((start, index))
            start = index + 1
    spans.append((start, len(line)))
    cells = []
    for index, (a, b) in enumerate(spans):
        segment = line[a:b]
        if not segment.strip() and index in (0, len(spans) - 1):
            continue  # 바깥쪽 파이프 앞뒤의 빈 칸
        left = a + (len(segment) - len(segment.lstrip()))
        right = b - (len(segment) - len(segment.rstrip()))
        cells.append((left, max(left, right)))
    return cells


def parse_text(path: Path, fmt: str, encoding: str | None = None) -> ParseResult:
    result = ParseResult(format=fmt, status="ok")
    data = path.read_bytes()
    try:
        text = data.decode(encoding or "utf-8-sig")
    except (UnicodeDecodeError, LookupError):
        result.status = "failed"
        result.error = (
            f"{encoding or 'UTF-8'} 디코딩에 실패했습니다. --encoding 옵션으로 인코딩을 지정해 다시 실행하세요 (예: cp949)."
        )
        return result

    lines: list[tuple[int, int, str]] = []  # (char_start, line_no, content without newline)
    position = 0
    for number, raw in enumerate(text.splitlines(keepends=True), start=1):
        content = raw.rstrip("\r\n")
        lines.append((position, number, content))
        position += len(raw)

    is_md = fmt == "md"
    blocks: list[Block] = []
    section_index = 0
    section_title: str | None = None
    list_seq = 0

    def emit(kind: str, start: int, end: int, line_start: int, line_end: int, **extra) -> Block:
        block = Block(
            kind=kind,
            text=text[start:end],
            locator={"format": fmt, "line_start": line_start, "line_end": line_end,
                     "char_start": start, "char_end": end},
            section_key=f"h{section_index}",
            section_title=section_title,
            hard_newlines=False,
            **extra,
        )
        blocks.append(block)
        return block

    pending: list[tuple[int, int, str]] = []  # 일반 문단으로 모으는 행

    def flush_paragraph() -> None:
        if not pending:
            return
        widths = [_display_width(content.strip()) for _, _, content in pending]
        max_width = max(widths) if widths else 0
        group = [pending[0]]
        for previous, current in zip(pending, pending[1:]):
            prev_text = previous[2].rstrip()
            hard = prev_text.endswith(_TERMINAL) or _display_width(prev_text.strip()) < 0.6 * max_width
            if hard:
                _emit_group(group)
                group = [current]
            else:
                group.append(current)
        _emit_group(group)
        pending.clear()

    def _emit_group(group: list[tuple[int, int, str]]) -> None:
        first_start, first_no, first_content = group[0]
        last_start, last_no, last_content = group[-1]
        start = first_start + (len(first_content) - len(first_content.lstrip()))
        end = last_start + len(last_content.rstrip())
        if end > start:
            emit("paragraph", start, end, first_no, last_no)

    index = 0
    in_fence = False
    while index < len(lines):
        start, number, content = lines[index]
        stripped = content.strip()

        if is_md and _MD_FENCE.match(content):
            flush_paragraph()
            in_fence = not in_fence
            index += 1
            continue
        if in_fence:
            if stripped:
                pending.append(lines[index])
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if is_md:
            heading = _MD_HEADING.match(content)
            if heading:
                flush_paragraph()
                title_start = start + heading.start(2)
                title_end = start + heading.end(2)
                section_index += 1
                section_title = text[title_start:title_end]
                emit("heading", title_start, title_end, number, number, level=len(heading.group(1)))
                index += 1
                continue
            # 표: 머리행 + 구분행
            if "|" in content and index + 1 < len(lines) and _MD_TABLE_SEP.match(lines[index + 1][2]):
                flush_paragraph()
                header_spans = _split_md_row(content)
                headers = [content[a:b] for a, b in header_spans]
                row_index = 0
                table_rows = [lines[index]]
                cursor = index + 2
                while cursor < len(lines) and "|" in lines[cursor][2] and lines[cursor][2].strip():
                    table_rows.append(lines[cursor])
                    cursor += 1
                for row_start, row_no, row_content in table_rows:
                    spans = _split_md_row(row_content)
                    if not spans:
                        continue
                    first, last = spans[0][0], spans[-1][1]
                    cells = []
                    for col, (a, b) in enumerate(spans):
                        header = headers[col] if row_index > 0 and col < len(headers) else None
                        cells.append({"col": col, "start": a - first, "end": b - first, "header": header})
                    block = emit("table_row", row_start + first, row_start + last, row_no, row_no,
                                 cells=cells, flags=["table_header"] if row_index == 0 else [])
                    block.locator["table_row"] = row_index
                    row_index += 1
                index = cursor
                continue

        marker_kind, body_start, _marker = strip_list_marker(content)
        if marker_kind:
            flush_paragraph()
            indent = len(content) - len(content.lstrip())
            item_start = start + body_start
            item_end = start + len(content.rstrip())
            last_no = number
            cursor = index + 1
            # 들여쓴 이어지는 행은 같은 항목에 붙인다.
            while cursor < len(lines):
                nxt_start, nxt_no, nxt_content = lines[cursor]
                if not nxt_content.strip() or strip_list_marker(nxt_content)[0]:
                    break
                if len(nxt_content) - len(nxt_content.lstrip()) <= indent:
                    break
                item_end = nxt_start + len(nxt_content.rstrip())
                last_no = nxt_no
                cursor += 1
            if not blocks or blocks[-1].kind != "list_item" or blocks[-1].locator["line_end"] != number - 1:
                list_seq += 1
            emit("list_item", item_start, item_end, number, last_no,
                 level=indent // 2, container=f"list{list_seq}")
            index = cursor
            continue

        pending.append(lines[index])
        index += 1
    flush_paragraph()

    result.blocks = [block for block in blocks if block.text.strip()]
    result.coverage = {
        "unit_type": "line",
        "units_total": len(lines),
        "units_processed": len(lines),
        "units_failed": 0,
        "blocks": len(result.blocks),
        "scope": {},
    }
    return finalize_status(result)
