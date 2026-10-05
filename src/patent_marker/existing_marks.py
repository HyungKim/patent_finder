"""입력 문서에 이미 있는 마킹을 찾아 걷어낸다 (변경안 A6).

분석과 사본 마킹은 항상 '마킹을 모두 걷어낸 상태'에서 시작한다.

- 입력 파일은 고치지 않는다. 메모리에 읽은 내용에서만 걷어내고, 결과는 별도 파일(마킹 사본)에만 반영된다.
- 걷어내는 것: 이 도구가 만든 표시(꼬리표·테두리·요약 슬라이드), 글자 강조색(형광펜), 메모, PDF 표시 주석.
- 걷어내지 않는 것: 일반 도형과 잉크(내용인지 표시인지 구분할 수 없다), PDF 링크·양식 필드·첨부,
  페이지 내용으로 그려진 색칠.

PPTX 파서와 사본 마킹이 같은 함수를 쓰므로 두 단계가 보는 문서 구조(슬라이드 번호, 도형)는 항상 같다.
"""
from __future__ import annotations

from typing import Any

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

TOOL_AUTHOR = "patent-marker"
TOOL_MARK_PREFIX = "PM_MARK_"
TOOL_SUMMARY_PREFIX = "PM_SUMMARY_"
_COMMENT_RELTYPES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",  # 기존 메모
    "http://schemas.microsoft.com/office/2018/10/relationships/comments",  # 최신(스레드) 메모
)
# 검토자가 덧붙이는 표시 주석. 링크(/Link), 양식(/Widget), 첨부(/FileAttachment) 등은 남긴다.
PDF_MARK_SUBTYPES = frozenset({
    "Text", "FreeText", "Line", "Square", "Circle", "Polygon", "PolyLine", "Highlight", "Underline",
    "Squiggly", "StrikeOut", "Stamp", "Caret", "Ink", "Redact", "Popup",
})


def total_marks(counts: dict[str, int] | None) -> int:
    return sum((counts or {}).values())


def describe_marks(counts: dict[str, int]) -> str:
    names = {"tool_shapes": "도구 표시", "tool_slides": "요약 슬라이드", "highlights": "형광펜",
             "comments": "메모", "tool_annotations": "도구 주석", "annotations": "주석"}
    return ", ".join(f"{names.get(key, key)} {value}" for key, value in counts.items() if value)


# ====================================================================== PPTX
def _named_shapes(slide: Any) -> list[tuple[Any, str]]:
    """(도형 요소, 이름). 그룹 안의 도형도 포함한다."""
    tree = slide.shapes._spTree
    result = []
    for properties in tree.iter(P + "cNvPr"):
        container = properties.getparent()
        shape = container.getparent() if container is not None else None
        if shape is None or shape is tree:
            continue
        result.append((shape, properties.get("name") or ""))
    return result


def _is_tool_slide(slide: Any) -> bool:
    """이 도구가 덧붙인 요약 슬라이드: 최상위 도형이 모두 PM_SUMMARY_ 도형이다."""
    tree = slide.shapes._spTree
    top = [(shape, name) for shape, name in _named_shapes(slide) if shape.getparent() is tree]
    return bool(top) and all(name.startswith(TOOL_SUMMARY_PREFIX) for _, name in top)


def _delete_slide(presentation: Any, slide: Any) -> None:
    id_list = presentation.slides._sldIdLst
    for slide_id in list(id_list):
        if presentation.part.related_part(slide_id.rId) is slide.part:
            presentation.part.drop_rel(slide_id.rId)
            id_list.remove(slide_id)
            return


def _drop_comments(slide: Any) -> int:
    """슬라이드의 메모 파트 연결을 끊는다. 연결이 끊긴 파트는 저장할 때 빠진다."""
    from lxml import etree

    count = 0
    for relationship_id, relationship in list(slide.part.rels.items()):
        if relationship.is_external or relationship.reltype not in _COMMENT_RELTYPES:
            continue
        try:
            root = etree.fromstring(relationship.target_part.blob,
                                    etree.XMLParser(resolve_entities=False, no_network=True))
            found = sum(1 for element in root.iter() if isinstance(element.tag, str)
                        and etree.QName(element).localname == "cm")
        except Exception:
            found = 0
        count += max(1, found)
        # 최신 메모는 슬라이드 XML의 확장 목록에서 관계 id를 참조한다. 참조를 먼저 지운다.
        for element in list(slide._element.iter()):
            if isinstance(element.tag, str) and element.get(R_ID) == relationship_id:
                parent = element.getparent()
                parent.remove(element)
                while parent is not None and len(parent) == 0 and parent.tag in (P + "ext", P + "extLst"):
                    grandparent = parent.getparent()
                    grandparent.remove(parent)
                    parent = grandparent
        slide.part.drop_rel(relationship_id)
    return count


def clear_pptx_marks(presentation: Any) -> dict[str, int]:
    """메모리에 읽은 프레젠테이션에서 기존 마킹을 걷어내고 종류별 건수를 돌려준다."""
    counts = {"tool_shapes": 0, "tool_slides": 0, "highlights": 0, "comments": 0}
    for slide in list(presentation.slides):
        if _is_tool_slide(slide):
            _delete_slide(presentation, slide)
            counts["tool_slides"] += 1
            continue
        for shape, name in _named_shapes(slide):
            if name.startswith((TOOL_MARK_PREFIX, TOOL_SUMMARY_PREFIX)) and shape.getparent() is not None:
                shape.getparent().remove(shape)
                counts["tool_shapes"] += 1
        for highlight in list(slide._element.iter(A + "highlight")):
            highlight.getparent().remove(highlight)
            counts["highlights"] += 1
        counts["comments"] += _drop_comments(slide)
    return counts


# ====================================================================== PDF
def count_pdf_marks(annotations: list[tuple[str | None, str | None]]) -> dict[str, int]:
    """(subtype, 작성자) 목록에서 걷어낼 표시 주석 수를 센다. Popup은 딸린 창이라 세지 않는다."""
    counts = {"tool_annotations": 0, "annotations": 0}
    for subtype, author in annotations:
        if subtype not in PDF_MARK_SUBTYPES or subtype == "Popup":
            continue
        counts["tool_annotations" if author == TOOL_AUTHOR else "annotations"] += 1
    return counts


def clear_pdf_marks(writer: Any) -> dict[str, int]:
    """pypdf writer의 모든 페이지에서 표시 주석을 걷어낸다. 링크·양식 필드는 남긴다."""
    from pypdf.generic import ArrayObject, NameObject

    seen: list[tuple[str | None, str | None]] = []
    for page in writer.pages:
        annotations = page.get("/Annots")
        if annotations is None:
            continue
        kept = ArrayObject()
        for reference in annotations.get_object():
            annotation = reference.get_object()
            subtype = str(annotation.get("/Subtype", "")).lstrip("/")
            if subtype in PDF_MARK_SUBTYPES:
                author = annotation.get("/T")
                seen.append((subtype, str(author) if author is not None else None))
            else:
                kept.append(reference)
        if len(kept):
            page[NameObject("/Annots")] = kept
        else:
            del page["/Annots"]
    return count_pdf_marks(seen)
