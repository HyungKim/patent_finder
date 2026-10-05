"""텍스트 PDF 파서. 페이지별 문자·좌표에서 줄, 다단 읽기 순서, 문단, 표를 복원한다 (스펙 4.1, 4.2).

- 좌표: x는 pdfminer 레이아웃 좌표(pt), top/bottom은 페이지 위쪽 기준, y0/y1은 아래쪽 기준.
- 줄바꿈으로 나뉜 줄은 공백 하나로 잇는다. 줄 끝 하이픈 제거와 반복 머리말 삭제는 내역을 기록한다.
- 스캔 페이지와 글리프 매핑이 깨진 페이지는 'OCR 필요'로 표시하며 조용히 건너뛰지 않는다.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..existing_marks import count_pdf_marks, describe_marks, total_marks
from .base import Block, ParseResult, UnsupportedDocument, finalize_status, strip_list_marker

_MAX_VECTOR_OBJECTS = 5000
EDGE_ZONE = 0.08  # 페이지 위·아래 8%: 머리말·꼬리말 후보 영역
_DIGITS = re.compile(r"\d+")


@dataclass
class Char:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    y0: float
    y1: float
    size: float


@dataclass
class Fragment:
    """같은 줄에서 가로로 이어진 문자 묶음, 또는 표 하나."""

    x0: float
    x1: float
    top: float
    bottom: float
    chars: list[Char] = field(default_factory=list)
    line_id: int = -1
    table: Any = None


@dataclass
class Line:
    text: str
    xs: list[float]
    top: float
    bottom: float
    y0: float
    y1: float
    size: float

    @property
    def x0(self) -> float:
        return self.xs[0]

    @property
    def x1(self) -> float:
        return self.xs[-1]


# ------------------------------------------------------------------ 문자 → 줄
def cluster_lines(chars: list[Char]) -> list[list[Char]]:
    """세로로 절반 이상 겹치는 문자를 같은 줄로 묶는다."""
    lines: list[dict[str, Any]] = []
    for char in sorted(chars, key=lambda c: (c.top, c.x0)):
        height = char.bottom - char.top
        if height <= 0:
            continue
        target = None
        for line in reversed(lines[-8:]):
            overlap = min(line["bottom"], char.bottom) - max(line["top"], char.top)
            base = min(height, line["bottom"] - line["top"])
            if base > 0 and overlap / base >= 0.5:
                target = line
                break
        if target is None:
            lines.append({"top": char.top, "bottom": char.bottom, "chars": [char]})
        else:
            target["chars"].append(char)
            if height > target["bottom"] - target["top"]:
                target["top"], target["bottom"] = char.top, char.bottom
    return [line["chars"] for line in lines]


def split_fragments(line_chars: list[Char], line_id: int) -> list[Fragment]:
    """한 줄의 문자를 큰 가로 간격에서 끊어 조각으로 만든다 (다단·표 열 분리용)."""
    ordered = sorted((c for c in line_chars), key=lambda c: c.x0)
    groups: list[list[Char]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if current.x0 - previous.x1 > 1.4 * max(previous.size, current.size):
            groups.append([current])
        else:
            groups[-1].append(current)
    fragments = []
    for group in groups:
        visible = [c for c in group if not c.text.isspace()]
        if not visible:
            continue
        fragments.append(Fragment(
            x0=min(c.x0 for c in visible), x1=max(c.x1 for c in visible),
            top=min(c.top for c in visible), bottom=max(c.bottom for c in visible),
            chars=group, line_id=line_id,
        ))
    return fragments


def build_line(chars: list[Char]) -> Line | None:
    """문자 목록에서 줄 텍스트와 문자 경계 x좌표(xs)를 만든다. 단어 사이 공백은 간격으로 복원한다."""
    ordered = sorted(chars, key=lambda c: c.x0)
    text: list[str] = []
    xs: list[float] = []
    previous: Char | None = None
    last_x1 = 0.0
    for char in ordered:
        if char.text.isspace() or not char.text:
            if text and text[-1] != " ":
                text.append(" ")
                xs.append(char.x0)
            previous = None
            continue
        if previous is not None and char.x0 - previous.x1 > 0.18 * max(previous.size, char.size):
            if text and text[-1] != " ":
                text.append(" ")
                xs.append(previous.x1)
        count = len(char.text)
        step = (char.x1 - char.x0) / count
        for index, ch in enumerate(char.text):
            text.append(ch)
            xs.append(char.x0 + step * index)
        last_x1 = char.x1
        previous = char
    while text and text[-1] == " ":
        text.pop()
        xs.pop()
    if not text:
        return None
    xs.append(last_x1)
    visible = [c for c in ordered if not c.text.isspace()]
    return Line(
        text="".join(text), xs=[round(x, 2) for x in xs],
        top=min(c.top for c in visible), bottom=max(c.bottom for c in visible),
        y0=min(c.y0 for c in visible), y1=max(c.y1 for c in visible),
        size=statistics.median(c.size for c in visible),
    )


# ------------------------------------------------------------------ XY-cut 읽기 순서
def lower_quartile(values: list[float]) -> float:
    """일반적인 줄 간격의 추정값. 문단 간격이 섞여 있어도 흔들리지 않게 하위 사분위를 쓴다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 4]


def _gaps(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a[1], b[0]) for a, b in zip(merged, merged[1:])]


def xy_cut(fragments: list[Fragment], x_gap: float, y_gap: float, min_col_width: float,
           stats: dict[str, int], column: str = "c") -> list[tuple[str, list[Fragment]]]:
    """재귀 XY-cut. 세로 여백(단 구분)을 먼저 찾고, 없으면 가장 큰 가로 여백에서 위아래로 나눈다.

    반환: (단 키, 조각 목록). 좌우로 나뉠 때만 단 키가 달라지고, 위아래로 나뉜 잎들은 같은 단 키를 갖는다.
    글머리 항목 사이 간격으로 잎이 잘게 나뉘어도 같은 단의 목록은 하나의 목록으로 다룰 수 있다.
    """
    if len(fragments) <= 1:
        return [(column, fragments)] if fragments else []
    x_candidates = sorted(_gaps([(f.x0, f.x1) for f in fragments]), key=lambda g: g[0] - g[1])
    for start, end in x_candidates:
        if end - start < x_gap:
            break
        left = [f for f in fragments if f.x1 <= start + 0.01]
        right = [f for f in fragments if f.x1 > start + 0.01]
        widths = [max(f.x1 for f in side) - min(f.x0 for f in side) for side in (left, right)]
        if min(widths) >= min_col_width:
            stats["x_cuts"] = stats.get("x_cuts", 0) + 1
            return (xy_cut(left, x_gap, y_gap, min_col_width, stats, column + "L")
                    + xy_cut(right, x_gap, y_gap, min_col_width, stats, column + "R"))
    y_candidates = sorted(_gaps([(f.top, f.bottom) for f in fragments]), key=lambda g: g[0] - g[1])
    if y_candidates and y_candidates[0][1] - y_candidates[0][0] >= y_gap:
        start = y_candidates[0][0]
        upper = [f for f in fragments if f.bottom <= start + 0.01]
        lower = [f for f in fragments if f.bottom > start + 0.01]
        return (xy_cut(upper, x_gap, y_gap, min_col_width, stats, column)
                + xy_cut(lower, x_gap, y_gap, min_col_width, stats, column))
    return [(column, fragments)]


# ------------------------------------------------------------------ 줄 → 문단
def _first_word_width(line: Line) -> float:
    space = line.text.find(" ")
    end = space if space > 0 else len(line.text)
    return line.xs[end] - line.xs[0]


def group_paragraphs(lines: list[Line], right_edge: float) -> list[list[Line]]:
    """같은 영역 안의 줄을 문단으로 묶는다."""
    if not lines:
        return []
    gaps = [b.top - a.bottom for a, b in zip(lines, lines[1:])]
    median_gap = lower_quartile([g for g in gaps if g > 0])
    paragraphs: list[list[Line]] = [[lines[0]]]
    for previous, current in zip(lines, lines[1:]):
        size = max(previous.size, current.size)
        first = paragraphs[-1][0]
        starts_marker = bool(strip_list_marker(current.text)[0])
        size_change = abs(previous.size - current.size) > 0.15 * size
        big_gap = (current.top - previous.bottom) > max(0.5 * size, 1.5 * median_gap + 0.5)
        # 다음 줄의 첫 단어가 앞줄에 들어갈 자리가 있었다면 의도적인 줄바꿈이다.
        room = right_edge - previous.x1
        hyphenated = (len(previous.text) >= 2 and previous.text[-1] == "-" and previous.text[-2].isalpha()
                      and current.text[:1].isascii() and current.text[:1].islower())
        hard_break = not hyphenated and room > _first_word_width(current) + 0.6 * size
        hanging = bool(strip_list_marker(first.text)[0])
        indented = not hanging and current.x0 - previous.x0 > 0.8 * size
        if starts_marker or size_change or big_gap or hard_break or indented:
            paragraphs.append([current])
        else:
            paragraphs[-1].append(current)
    return paragraphs


def join_lines(lines: list[Line]) -> tuple[str, list[dict[str, Any]], int]:
    """문단의 줄을 이어 텍스트와 줄별 위치를 만든다. 반환: (text, line locators, 하이픈 제거 수)."""
    pieces: list[str] = []
    locators: list[dict[str, Any]] = []
    position = 0
    dehyphenated = 0
    for index, line in enumerate(lines):
        text, xs = line.text, list(line.xs)
        if index == 0:
            kind, body_start, _marker = strip_list_marker(text)
            if kind == "bullet":
                text, xs = text[body_start:], xs[body_start:]
        is_last = index == len(lines) - 1
        joiner = "" if is_last else " "
        if not is_last and len(text) >= 2 and text[-1] in "-­":
            following = lines[index + 1].text[:1]
            if text[-1] == "­" or (text[-2].isascii() and text[-2].isalpha()
                                        and following.isascii() and following.islower()):
                text, xs = text[:-1], xs[:-1]
                joiner = ""
                dehyphenated += 1
        if text:
            locators.append({
                "start": position, "end": position + len(text), "xs": xs,
                "top": round(line.top, 2), "bottom": round(line.bottom, 2),
                "y0": round(line.y0, 2), "y1": round(line.y1, 2),
            })
        pieces.append(text + joiner)
        position += len(text) + len(joiner)
    return "".join(pieces).rstrip(), locators, dehyphenated


# ------------------------------------------------------------------ 페이지
class _PageParser:
    def __init__(self, page: Any, number: int) -> None:
        self.page = page
        self.number = number
        self.width = float(page.width)
        self.height = float(page.height)
        self.rotation = int(getattr(page, "rotation", 0) or 0)
        self.blocks: list[Block] = []
        self.meta: dict[str, Any] = {"dehyphenated": 0, "x_cuts": 0, "tables": 0}

    def parse(self) -> list[Block]:
        page = self.page
        raw = page.dedupe_chars(tolerance=1).chars
        upright = [c for c in raw if c.get("upright", True)]
        self.meta["rotated_chars"] = sum(1 for c in raw if not c.get("upright", True) and c["text"].strip())
        chars = [
            Char(c["text"], float(c["x0"]), float(c["x1"]), float(c["top"]), float(c["bottom"]),
                 float(c["y0"]), float(c["y1"]), float(c.get("size") or (c["bottom"] - c["top"])))
            for c in upright
        ]
        visible = [c for c in chars if c.text.strip()]
        unmapped = sum(1 for c in visible if c.text.startswith("(cid:"))
        self.meta["text_chars"] = len(visible)
        self.meta["unmapped_ratio"] = unmapped / len(visible) if visible else 0.0
        images = page.images
        area = self.width * self.height
        self.meta["image_ratio"] = min(1.0, sum(
            max(0.0, float(i["x1"]) - float(i["x0"])) * max(0.0, float(i["bottom"]) - float(i["top"]))
            for i in images) / area) if area else 0.0
        self.meta["images"] = len(images)
        self.meta["vectors"] = len(page.lines) + len(page.rects) + len(page.curves)
        if not visible or self.meta["unmapped_ratio"] > 0.2:
            return []

        body_size = statistics.median(c.size for c in visible)
        tables = self._tables(chars)
        table_chars = {id(c) for table in tables for c in table["chars"]}
        free = [c for c in chars if id(c) not in table_chars]

        # 머리말·꼬리말 영역의 줄은 단 나눔의 영향을 받지 않도록 본문 배치 분석에서 떼어 둔다.
        fragments: list[Fragment] = []
        header_lines: list[Line] = []
        footer_lines: list[Line] = []
        y_lines = []
        for line_chars in cluster_lines(free):
            shown = [c for c in line_chars if c.text.strip()]
            if not shown:
                continue
            if max(c.bottom for c in shown) < EDGE_ZONE * self.height:
                header_lines.append(build_line(line_chars))
            elif min(c.top for c in shown) > (1 - EDGE_ZONE) * self.height:
                footer_lines.append(build_line(line_chars))
            else:
                y_lines.append(line_chars)
        tops = []
        for line_id, line_chars in enumerate(y_lines):
            parts = split_fragments(line_chars, line_id)
            fragments.extend(parts)
            if parts:
                tops.append((min(p.top for p in parts), max(p.bottom for p in parts)))
        for table in tables:
            x0, top, x1, bottom = table["bbox"]
            fragments.append(Fragment(x0=x0, x1=x1, top=top, bottom=bottom, table=table))

        tops.sort()
        median_gap = lower_quartile([b[0] - a[1] for a, b in zip(tops, tops[1:]) if b[0] - a[1] > 0])
        stats: dict[str, int] = {}
        leaves = xy_cut(
            fragments,
            x_gap=max(1.5 * body_size, 10.0),
            y_gap=max(0.6 * body_size, 1.6 * median_gap + 1.0),
            min_col_width=max(8 * body_size, 0.15 * self.width),
            stats=stats,
        )
        self.meta["x_cuts"] = stats.get("x_cuts", 0)

        # 잎마다 줄과 문단을 먼저 만들고, 목록 수준은 같은 단 전체의 왼쪽 끝을 기준으로 정한다.
        prepared = [(column, self._leaf_items(leaf)) for column, leaf in leaves]
        column_left: dict[str, float] = {}
        for column, items in prepared:
            for _top, item in items:
                if not isinstance(item, dict):
                    column_left[column] = min(column_left.get(column, float("inf")), min(line.x0 for line in item))
        for line in header_lines:
            self._paragraph([line], body_size, None, line.x0)
        for column, items in prepared:
            for _top, item in items:
                if isinstance(item, dict):
                    self._table_blocks(item)
                else:
                    self._paragraph(item, body_size, column, column_left[column])
        for line in footer_lines:
            self._paragraph([line], body_size, None, line.x0)
        self._mark_title(body_size)
        return self.blocks

    # ---- 표 ----
    def _tables(self, chars: list[Char]) -> list[dict[str, Any]]:
        page = self.page
        try:
            vector_count = self.meta.get("vectors", 0)
            if vector_count == 0:
                return []
            if vector_count > _MAX_VECTOR_OBJECTS:
                self.meta["table_detection_skipped"] = True
                return []
            found = page.find_tables()
        except Exception:
            return []
        tables = []
        for table in found:
            rows = [[cell for cell in row.cells] for row in table.rows]
            if len(rows) < 2 or max((sum(1 for c in row if c) for row in rows), default=0) < 2:
                continue
            x0, top, x1, bottom = (float(v) for v in table.bbox)
            inside = [c for c in chars if x0 <= (c.x0 + c.x1) / 2 <= x1 and top <= (c.top + c.bottom) / 2 <= bottom]
            tables.append({"bbox": (x0, top, x1, bottom), "rows": rows, "chars": inside, "index": len(tables)})
        self.meta["tables"] = len(tables)
        return tables

    def _table_blocks(self, table: dict[str, Any]) -> None:
        headers: dict[int, str] = {}
        for row_index, row in enumerate(table["rows"]):
            pieces: list[str] = []
            cells: list[dict[str, Any]] = []
            position = 0
            for col, bbox in enumerate(row):
                if not bbox:
                    continue
                x0, top, x1, bottom = (float(v) for v in bbox)
                cell_chars = [c for c in table["chars"]
                              if x0 <= (c.x0 + c.x1) / 2 <= x1 and top <= (c.top + c.bottom) / 2 <= bottom]
                lines = [line for line in (build_line(group) for group in cluster_lines(cell_chars)) if line]
                if not lines:
                    continue
                text, locators, removed = join_lines(sorted(lines, key=lambda l: l.top))
                self.meta["dehyphenated"] += removed
                if not text:
                    continue
                if row_index == 0:
                    headers[col] = text
                if pieces:
                    pieces.append(" | ")
                    position += 3
                for locator in locators:
                    locator["start"] += position
                    locator["end"] += position
                cells.append({"col": col, "start": position, "end": position + len(text),
                              "header": None if row_index == 0 else headers.get(col), "lines": locators})
                pieces.append(text)
                position += len(text)
            if pieces:
                self.blocks.append(Block(
                    kind="table_row", text="".join(pieces), unit=self.number,
                    locator=self._locator(table=table["index"], row=row_index,
                                          lines=[line for cell in cells for line in cell.pop("lines")]),
                    cells=cells, flags=["table_header"] if row_index == 0 else [],
                    section_key=f"page:{self.number}", hard_newlines=False,
                ))

    # ---- 본문 ----
    def _locator(self, **extra: Any) -> dict[str, Any]:
        locator: dict[str, Any] = {"format": "pdf", "page": self.number}
        if self.rotation:
            locator["rotation"] = self.rotation
        locator.update(extra)
        return locator

    def _leaf_items(self, leaf: list[Fragment]) -> list[tuple[float, Any]]:
        """잎 하나의 내용: (위쪽 좌표, 표 또는 문단의 줄 묶음)을 위에서 아래 순으로."""
        by_line: dict[int, list[Char]] = {}
        for fragment in leaf:
            if fragment.table is None:
                by_line.setdefault(fragment.line_id, []).extend(fragment.chars)
        lines = sorted((line for line in (build_line(chars) for chars in by_line.values()) if line),
                       key=lambda l: l.top)
        items: list[tuple[float, Any]] = [(f.top, f.table) for f in leaf if f.table is not None]
        right_edge = max((line.x1 for line in lines), default=0.0)
        for paragraph in group_paragraphs(lines, right_edge):
            items.append((paragraph[0].top, paragraph))
        return sorted(items, key=lambda entry: entry[0])

    def _paragraph(self, item: list[Line], body_size: float, column: str | None, left_edge: float) -> None:
        """줄 묶음 하나를 블록으로 만든다."""
        marker_kind = strip_list_marker(item[0].text)[0]
        text, locators, removed = join_lines(item)
        self.meta["dehyphenated"] += removed
        if not text.strip():
            return
        size = statistics.median(line.size for line in item)
        if marker_kind:
            kind = "list_item"
        elif size >= 1.2 * body_size and len(text) <= 120:
            kind = "heading"
        else:
            kind = "paragraph"
        # 목록 수준은 들여쓰기로 추정한다.
        level = int((item[0].x0 - left_edge) / (1.2 * body_size) + 0.5) if marker_kind else 0
        self.blocks.append(Block(
            kind=kind, text=text, unit=self.number, locator=self._locator(lines=locators),
            section_key=f"page:{self.number}", hard_newlines=False, level=max(0, level),
            container=f"p{self.number}:{column}" if marker_kind and column is not None else None,
        ))

    def _mark_title(self, body_size: float) -> None:
        title = None
        for block in self.blocks:
            if block.kind == "heading" and block.locator["lines"] and \
                    block.locator["lines"][0]["top"] < 0.35 * self.height:
                block.kind = "title"
                title = block.text.strip()
                break
        for block in self.blocks:
            block.section_title = title


def _is_password_error(exc: BaseException) -> bool:
    seen: list[BaseException] = [exc]
    seen.extend(arg for arg in exc.args if isinstance(arg, BaseException))
    if exc.__cause__:
        seen.append(exc.__cause__)
    return any("Password" in type(item).__name__ or "Encrypt" in type(item).__name__ for item in seen)


def _edge_key(block: Block, height: float) -> str | None:
    lines = block.locator.get("lines") or []
    if not lines or len(block.text) > 80:
        return None
    if lines[-1]["bottom"] < EDGE_ZONE * height or lines[0]["top"] > (1 - EDGE_ZONE) * height:
        return _DIGITS.sub("#", " ".join(block.text.split()))
    return None


def parse_pdf(path: Path, config: AppConfig) -> ParseResult:
    import pdfplumber

    result = ParseResult(format="pdf", status="ok")
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:
        if _is_password_error(exc):
            raise UnsupportedDocument("encrypted", "암호화된 PDF는 지원하지 않습니다.") from exc
        raise

    failed: list[int] = []
    ocr_needed: list[int] = []
    rotated_only: list[int] = []
    multi_column: list[int] = []
    rotated_pages: list[int] = []
    page_blocks: list[tuple[int, float, list[Block]]] = []
    annotations: list[tuple[str | None, str | None]] = []
    totals = {"dehyphenated": 0, "rotated_chars": 0, "tables": 0, "images": 0}
    with pdf:
        total = len(pdf.pages)
        if total > config.limits.max_units:
            raise UnsupportedDocument(
                "unit_limit", f"페이지 수({total})가 제한({config.limits.max_units})을 넘습니다. limits.max_units를 확인하세요."
            )
        for number, page in enumerate(pdf.pages, start=1):
            parser = _PageParser(page, number)
            try:
                for annotation in page.annots:
                    subtype = (annotation.get("data") or {}).get("Subtype")
                    annotations.append((getattr(subtype, "name", None), annotation.get("title")))
            except Exception:  # 손상된 주석은 건수에서만 빠진다
                pass
            try:
                blocks = parser.parse()
            except Exception as exc:  # 한 페이지의 오류가 전체를 막지 않게 한다
                failed.append(number)
                result.warn("page_failed", f"{number}쪽 처리 실패: {type(exc).__name__}", unit=number)
                continue
            finally:
                page.flush_cache()
            meta = parser.meta
            for key in ("dehyphenated", "rotated_chars", "tables", "images"):
                totals[key] += meta.get(key, 0)
            if meta.get("unmapped_ratio", 0) > 0.2:
                ocr_needed.append(number)
                result.warn("unmapped_glyphs", f"{number}쪽: 글꼴 매핑이 없어 텍스트를 복원할 수 없습니다 (OCR 필요).", unit=number)
            elif meta.get("text_chars", 0) < 20 and meta.get("image_ratio", 0) >= 0.2:
                ocr_needed.append(number)
                result.warn("ocr_needed", f"{number}쪽: 텍스트가 거의 없는 이미지 페이지입니다 (OCR 미지원).", unit=number)
            elif meta.get("text_chars", 0) == 0 and (meta.get("images") or meta.get("vectors")):
                ocr_needed.append(number)
                result.warn("ocr_needed", f"{number}쪽: 텍스트 없이 도형·이미지만 있는 페이지입니다 (분석 불가).", unit=number)
            elif meta.get("image_ratio", 0) >= 0.3:
                result.warn("images_not_analyzed", f"{number}쪽: 이미지 영역 안의 텍스트는 분석되지 않았습니다.", unit=number)
            if meta.get("x_cuts"):
                multi_column.append(number)
            if parser.rotation or meta.get("rotated_chars"):
                rotated_pages.append(number)
            if meta.get("rotated_chars") and not blocks:
                rotated_only.append(number)
                result.warn("rotated_page_not_analyzed",
                            f"{number}쪽: 본문이 회전되어 있어 분석하지 못했습니다. 페이지를 바르게 돌린 뒤 다시 시도하세요.",
                            unit=number)
            if meta.get("table_detection_skipped"):
                result.warn("table_detection_skipped", f"{number}쪽: 선 개체가 많아 표 인식을 건너뛰었습니다.", unit=number)
            page_blocks.append((number, parser.height, blocks))

    # 반복 머리말·꼬리말
    removed: list[dict[str, Any]] = []
    if not config.parsing.include_headers_footers and len(page_blocks) >= 3:
        counts: dict[str, set[int]] = {}
        for number, height, blocks in page_blocks:
            for block in blocks:
                key = _edge_key(block, height)
                if key:
                    counts.setdefault(key, set()).add(number)
        threshold = max(3, len(page_blocks) // 2)
        repeated = {key for key, pages in counts.items() if len(pages) >= threshold}
        if repeated:
            for index, (number, height, blocks) in enumerate(page_blocks):
                page_blocks[index] = (number, height, [b for b in blocks if _edge_key(b, height) not in repeated])
            removed = [{"text": key, "units": len(counts[key])} for key in sorted(repeated)]

    for _number, _height, blocks in page_blocks:
        result.blocks.extend(blocks)

    if multi_column:
        result.warn("multi_column", f"다단/좌우 배치 감지({len(multi_column)}쪽): 읽기 순서를 확인하세요.")
    if rotated_pages:
        result.warn("rotated_text", f"회전된 페이지·문자 감지({len(rotated_pages)}쪽): 세로 문자는 분석에서 제외되었고 위치가 부정확할 수 있습니다.")
    result.coverage = {
        "unit_type": "page",
        "units_total": total,
        "units_processed": len(page_blocks) - len(ocr_needed) - len(rotated_only),
        "units_failed": len(failed),
        "failed_units": failed,
        "units_ocr_needed": len(ocr_needed),
        "ocr_needed_units": ocr_needed,
        "multi_column_units": multi_column,
        "rotated_units": rotated_pages,
        "rotated_only_units": rotated_only,
        "rotated_chars_skipped": totals["rotated_chars"],
        "blocks": len(result.blocks),
        "tables": totals["tables"],
        "scope": {"headers_footers": config.parsing.include_headers_footers},
        "not_analyzed": {"images": totals["images"]},
        "edits": {"dehyphenated": totals["dehyphenated"], "removed_headers_footers": removed},
        "existing_marks": count_pdf_marks(annotations),
    }
    existing = result.coverage["existing_marks"]
    if total_marks(existing):
        result.warn("existing_marks_cleared",
                    f"기존 주석 {total_marks(existing)}건({describe_marks(existing)})은 분석에 쓰지 않았고 "
                    "마킹 사본에도 남기지 않습니다.")
    if total and len(ocr_needed) == total:
        result.status = "unsupported"
        result.error = "텍스트 레이어가 없는 스캔 PDF로 보입니다. 로컬 OCR은 아직 지원하지 않습니다."
        return result
    if total and len(ocr_needed) + len(rotated_only) + len(failed) == total:
        result.status = "unsupported"
        result.error = "분석할 수 있는 페이지가 없습니다 (회전된 본문 또는 텍스트 없는 페이지)."
        return result
    return finalize_status(result)
