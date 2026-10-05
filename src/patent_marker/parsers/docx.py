"""DOCX 파서. 본문 문단·목록·표를 문서 순서대로 추출한다 (스펙 4.1, 4.2).

python-docx는 파일을 여는 데만 쓰고, 텍스트는 XML을 직접 순회해 추적 변경 삽입분,
하이퍼링크, 콘텐츠 컨트롤 안의 run까지 포함한다. 삭제 변경분과 Fallback 복제본은 제외한다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from ..config import AppConfig
from .base import Block, ParseResult, check_zip_limits, finalize_status

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_SKIP = {W + "del", W + "moveFrom", W + "pPr", W + "rPr", W + "txbxContent", MC_FALLBACK}
_HEADING_STYLE = re.compile(r"^(heading|제목|title|subtitle|부제)\s*(\d+)?", re.IGNORECASE)
_LIST_STYLE = re.compile(r"^(list|목록)", re.IGNORECASE)


def paragraph_text(p: Any) -> tuple[str, list[dict[str, int]]]:
    """문단 텍스트와 run별 문자 구간을 구한다."""
    parts: list[str] = []
    runs: list[dict[str, int]] = []
    state = {"pos": 0, "run": -1}

    def walk(element: Any) -> None:
        for child in element:
            tag = child.tag
            if tag in _SKIP or not isinstance(tag, str):
                continue
            if tag == W + "r":
                state["run"] += 1
                start = state["pos"]
                for node in child:
                    node_tag = node.tag
                    if node_tag == W + "t":
                        piece = node.text or ""
                    elif node_tag == W + "tab":
                        piece = "\t"
                    elif node_tag in (W + "br", W + "cr"):
                        piece = "\n"
                    elif node_tag == W + "noBreakHyphen":
                        piece = "-"
                    else:
                        piece = ""
                    if piece:
                        parts.append(piece)
                        state["pos"] += len(piece)
                if state["pos"] > start:
                    runs.append({"run": state["run"], "start": start, "end": state["pos"]})
            else:
                walk(child)

    walk(p)
    return "".join(parts), runs


def _textboxes(p: Any) -> Iterator[Any]:
    for box in p.iter(W + "txbxContent"):
        if any(ancestor.tag == MC_FALLBACK for ancestor in box.iterancestors()):
            continue
        yield box


def _val(element: Any, child: str) -> str | None:
    if element is None:
        return None
    node = element.find(W + child)
    return None if node is None else node.get(W + "val")


class _DocxWalker:
    def __init__(self, document: Any, config: AppConfig) -> None:
        self.document = document
        self.config = config
        self.blocks: list[Block] = []
        self.section_index = 0
        self.section_title: str | None = None
        self.table_count = 0
        self.body_index = -1
        self.style_names: dict[str, str] = {}
        try:
            for style in document.styles:
                self.style_names[style.style_id] = style.name or ""
        except Exception:  # 손상된 styles 파트는 무시하고 스타일 없이 진행
            self.style_names = {}

    # ---- 공통 ----
    def _emit(self, kind: str, text: str, locator: dict[str, Any], **extra: Any) -> None:
        if not text.strip():
            return
        locator = {"format": "docx", **locator}
        self.blocks.append(Block(
            kind=kind, text=text, locator=locator,
            section_key=f"h{self.section_index}", section_title=self.section_title, **extra,
        ))

    # ---- 본문 ----
    def walk_body(self, body: Any) -> None:
        for child in body:
            if child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    self.walk_body(content)
                continue
            if child.tag == W + "p":
                self.body_index += 1
                self.paragraph(child, {"body_index": self.body_index})
            elif child.tag == W + "tbl":
                self.body_index += 1
                self.table(child, {"body_index": self.body_index, "table": self.table_count})
                self.table_count += 1

    def paragraph(self, p: Any, locator: dict[str, Any], kind_override: str | None = None,
                  flags: list[str] | None = None) -> None:
        text, runs = paragraph_text(p)
        ppr = p.find(W + "pPr")
        style_id = _val(ppr, "pStyle")
        style_name = self.style_names.get(style_id or "", style_id or "")
        kind, level, container = "paragraph", 0, None
        num_pr = ppr.find(W + "numPr") if ppr is not None else None
        outline = _val(ppr, "outlineLvl")
        heading = _HEADING_STYLE.match(style_name or "")
        if kind_override:
            kind = kind_override
        elif heading or (outline is not None and outline.isdigit() and int(outline) < 9):
            kind = "heading"
            level = int(heading.group(2)) if heading and heading.group(2) else int(outline or 0) + 1
        elif num_pr is not None or _LIST_STYLE.match(style_name or ""):
            kind = "list_item"
            ilvl = _val(num_pr, "ilvl")
            level = int(ilvl) if ilvl and ilvl.isdigit() else 0
            container = f"num{_val(num_pr, 'numId') or style_name}"
        if kind == "heading" and text.strip():
            self.section_index += 1
            self.section_title = text.strip()
        self._emit(kind, text, {**locator, "runs": runs, "style": style_name or None},
                   level=level, container=container, flags=list(flags or []))
        for box_index, box in enumerate(_textboxes(p)):
            for para_index, inner in enumerate(box.iterchildren(W + "p")):
                inner_text, inner_runs = paragraph_text(inner)
                self._emit("paragraph", inner_text,
                           {**locator, "textbox": box_index, "paragraph": para_index, "runs": inner_runs},
                           flags=["textbox"])

    # ---- 표 ----
    def table(self, tbl: Any, locator: dict[str, Any]) -> None:
        rows = list(tbl.iterchildren(W + "tr"))
        parsed_rows: list[tuple[list[dict[str, Any]], list[Any], bool]] = []
        for tr in rows:
            tr_pr = tr.find(W + "trPr")
            is_header = tr_pr is not None and tr_pr.find(W + "tblHeader") is not None
            grid_before = _val(tr_pr, "gridBefore")
            col = int(grid_before) if grid_before and grid_before.isdigit() else 0
            cells: list[dict[str, Any]] = []
            nested: list[tuple[int, int, Any]] = []  # (열, 셀 안 순번, 표)
            for tc in tr.iterchildren(W + "tc"):
                tc_pr = tc.find(W + "tcPr")
                span_value = _val(tc_pr, "gridSpan")
                span = int(span_value) if span_value and span_value.isdigit() else 1
                v_merge = tc_pr.find(W + "vMerge") if tc_pr is not None else None
                continuation = v_merge is not None and v_merge.get(W + "val") != "restart"
                if not continuation:
                    paragraphs = []
                    for para_index, p in enumerate(tc.iterchildren(W + "p")):
                        text, runs = paragraph_text(p)
                        if text.strip():
                            paragraphs.append({"index": para_index, "text": text, "runs": runs})
                    cells.append({"col": col, "span": span, "paragraphs": paragraphs})
                    nested.extend((col, index, inner) for index, inner in enumerate(tc.iterchildren(W + "tbl")))
                col += span
            parsed_rows.append((cells, nested, is_header))

        multi = len(parsed_rows) >= 2 and max((len(c) for c, _, _ in parsed_rows), default=0) >= 2
        header_rows = [i for i, (_, _, flag) in enumerate(parsed_rows) if flag]
        if not header_rows and multi:
            header_rows = [0]
        headers: dict[int, str] = {}
        for index in header_rows:
            for cell in parsed_rows[index][0]:
                label = " ".join(p["text"].strip() for p in cell["paragraphs"]).strip()
                if label:
                    headers[cell["col"]] = label

        for row_index, (cells, nested, _flag) in enumerate(parsed_rows):
            pieces: list[str] = []
            spans: list[dict[str, Any]] = []
            position = 0
            for cell in cells:
                cell_text = "\n".join(p["text"] for p in cell["paragraphs"])
                if not cell_text.strip():
                    continue
                if pieces:
                    pieces.append(" | ")
                    position += 3
                para_locs = []
                offset = position
                for p in cell["paragraphs"]:
                    para_locs.append({"index": p["index"], "start": offset,
                                      "end": offset + len(p["text"]), "runs": p["runs"]})
                    offset += len(p["text"]) + 1
                is_header_row = row_index in header_rows
                spans.append({
                    "col": cell["col"], "start": position, "end": position + len(cell_text),
                    "header": None if is_header_row else headers.get(cell["col"]),
                    "paragraphs": para_locs,
                })
                pieces.append(cell_text)
                position += len(cell_text)
            if pieces:
                self._emit("table_row", "".join(pieces), {**locator, "row": row_index},
                           cells=spans, flags=["table_header"] if row_index in header_rows else [])
            for col, index, inner in nested:
                # 중첩 표의 행은 자기 행 번호를 갖고, 바깥 표에서의 위치는 nested_path에 남긴다.
                path = list(locator.get("nested_path", [])) + [{"row": row_index, "col": col, "index": index}]
                self.table(inner, {**locator, "nested_path": path})


def parse_docx(path: Path, config: AppConfig) -> ParseResult:
    import docx
    from lxml import etree

    result = ParseResult(format="docx", status="ok")
    check_zip_limits(path, config.limits.max_uncompressed_mb)
    document = docx.Document(str(path))
    walker = _DocxWalker(document, config)
    body = document.element.body
    walker.walk_body(body)

    scope = {
        "headers_footers": config.parsing.include_headers_footers,
        "footnotes": config.parsing.include_footnotes,
    }
    if config.parsing.include_headers_footers:
        seen: set[str] = set()
        for section_index, section in enumerate(document.sections):
            for name in ("header", "footer", "first_page_header", "first_page_footer",
                         "even_page_header", "even_page_footer"):
                part = getattr(section, name)
                if part.is_linked_to_previous:
                    continue
                for para_index, paragraph in enumerate(part.paragraphs):
                    text, runs = paragraph_text(paragraph._p)
                    if text.strip() and text not in seen:
                        seen.add(text)
                        walker._emit("header_footer", text,
                                     {"section": section_index, "part": name, "paragraph": para_index, "runs": runs})
    if config.parsing.include_footnotes:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        for rel in document.part.rels.values():
            if rel.is_external or not rel.reltype.endswith("/footnotes"):
                continue
            root = etree.fromstring(rel.target_part.blob, parser)
            for note in root.iterchildren(W + "footnote"):
                if note.get(W + "type") in ("separator", "continuationSeparator"):
                    continue
                for para_index, p in enumerate(note.iterchildren(W + "p")):
                    text, runs = paragraph_text(p)
                    walker._emit("footnote", text,
                                 {"footnote_id": note.get(W + "id"), "paragraph": para_index, "runs": runs})

    pictures = sum(1 for _ in body.iter("{http://schemas.openxmlformats.org/drawingml/2006/picture}pic"))
    objects = sum(1 for _ in body.iter(W + "object"))
    result.blocks = walker.blocks
    result.coverage = {
        "unit_type": "block",
        "units_total": walker.body_index + 1,
        "units_processed": walker.body_index + 1,
        "units_failed": 0,
        "blocks": len(walker.blocks),
        "tables": walker.table_count,
        "scope": scope,
        "not_analyzed": {"pictures": pictures, "embedded_objects": objects},
    }
    if pictures:
        result.warn("images_not_analyzed", f"이미지 {pictures}개 안의 텍스트는 분석하지 않았습니다 (OCR 미지원).")
    if objects:
        result.warn("objects_not_analyzed", f"포함 개체 {objects}개(수식·OLE 등)는 분석하지 않았습니다.")
    return finalize_status(result)
