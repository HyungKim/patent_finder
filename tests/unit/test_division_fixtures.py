"""사업부별 합성 보고자료(30장 이상)가 PPTX와 PDF 양쪽에서 빠짐없이 추출되는지 확인한다.

이 자료로 찾은 PDF 파서 결함(전각 글머리 기호, 다단 쪽의 꼬리말, 항목 간격으로 끊긴 목록 계층)의 회귀 시험이다.
자료는 tests/fixtures/build_division_reports.py 로 만든 합성 자료이며 실제 사업 내용과 무관하다.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest

from helpers import FakeTokenizer, make_config
from patent_marker.config import SegmentationConfig
from patent_marker.parsers import parse_document
from patent_marker.segmentation.paragraphs import build_paragraphs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DIVISIONS = FIXTURES / "divisions"
KEY = FIXTURES / "division_expected_labels.csv"
OPTICS_PDF = "광학솔루션사업부_기술보고_합성.pdf"
OPTICS_PPTX = "광학솔루션사업부_기술보고_합성.pptx"


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    config = make_config(tmp_path_factory.mktemp("cfg"))
    return {path.name: parse_document(path, config) for path in sorted(DIVISIONS.iterdir())}


@pytest.fixture(scope="module")
def expected():
    with KEY.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_three_divisions_in_two_formats_with_at_least_thirty_pages(parsed):
    assert len(parsed) == 6 and {Path(name).suffix for name in parsed} == {".pptx", ".pdf"}
    for name, result in parsed.items():
        assert result.coverage["units_total"] >= 30, name
        assert result.status in ("ok", "partial") and result.coverage["units_failed"] == 0, name
        assert result.coverage["existing_marks"] == dict.fromkeys(result.coverage["existing_marks"], 0)
    deck = parsed[OPTICS_PPTX]
    assert deck.coverage["units_total"] == 34 and deck.coverage["hidden_units"] == [34]
    assert deck.coverage["ocr_needed_units"] == [32]  # 그림뿐인 장은 경고로 남는다
    assert parsed[OPTICS_PDF].coverage["units_total"] == 33  # 숨긴 장은 PDF에 없다


def test_every_expected_paragraph_is_extracted_in_both_formats(parsed, expected):
    for name, result in parsed.items():
        stem, fmt = name.rsplit(".", 1)
        by_unit: dict[int, list[str]] = {}
        for block in result.blocks:
            by_unit.setdefault(block.unit, []).append(_squeeze(block.text))
        missing = []
        for row in expected:
            if row["document"] != stem or not (row["scope"] == "both" or (row["scope"] == "pptx_hidden" and fmt == "pptx")):
                continue
            target = _squeeze(row["text"])
            texts = by_unit.get(int(row["slide"]), [])
            if not any(target in text or (len(text) >= 10 and text in target) for text in texts):
                missing.append((row["slide"], row["kind"], row["text"][:30]))
        assert not missing, (name, len(missing), missing[:3])


def test_pdf_bullets_keep_markers_out_and_hierarchy_in(parsed):
    page = [block for block in parsed[OPTICS_PDF].blocks if block.unit == 7]
    assert [(block.kind, block.level) for block in page] == [
        ("title", 0), ("list_item", 0), ("list_item", 0), ("list_item", 1), ("list_item", 1),
        ("list_item", 0), ("list_item", 0)]
    assert page[1].text.startswith("배경: 연속 촬영 시") and page[3].text.startswith("코일 온도는")  # 기호는 본문에서 제외
    assert len({block.container for block in page[1:]}) == 1  # 항목 사이 간격으로 잎이 나뉘어도 같은 목록
    paragraphs = build_paragraphs(page, FakeTokenizer(), SegmentationConfig())
    group = next(p for p in paragraphs if p.kind == "list_group")
    assert len(group.parts) == 3 and group.original_text.startswith("제안: 홀 센서 출력을")
    wrapped = next(b for b in parsed[OPTICS_PDF].blocks if b.unit == 13 and b.text.startswith("제안: 5개 영역"))
    assert wrapped.text.endswith("2회 반복 보정한다") and len(wrapped.locator["lines"]) == 2  # 줄바꿈된 항목은 한 문단


def test_pdf_footer_is_removed_on_every_page_including_two_column_pages(parsed):
    result = parsed[OPTICS_PDF]
    removed = result.coverage["edits"]["removed_headers_footers"]
    assert [item["units"] for item in removed] == [33] and "- # -" in removed[0]["text"]
    assert not any("실제 사업 내용과 무관" in block.text for block in result.blocks if block.unit != 1)
    assert not any(re.fullmatch(r"-\s*\d+\s*-", block.text) for block in result.blocks)
    page = [block.text for block in result.blocks if block.unit == 10]  # 좌우 2단
    assert page == ["자동 초점: 정착 시간 단축", "문제", "렌즈 무게 증가로 초점 정착 시간이 길어짐",
                    "촬영 자세에 따라 초점 위치 편차가 발생", "제안",
                    "가속도 센서로 촬영 자세를 판별하고 자세별 중력 보상 전류를 구동 지령에 더한다",
                    "정착 구간에서 목표 위치 오차의 부호가 바뀌는 시점에 제동 펄스를 인가하여 오버슈트를 줄인다"]
    assert 10 in result.coverage["multi_column_units"]


def test_pdf_and_pptx_tables_agree(parsed):
    def rows(result, unit):
        return [(block.text, [cell["header"] for cell in block.cells]) for block in result.blocks
                if block.unit == unit and block.kind == "table_row"]

    pdf_rows, pptx_rows = rows(parsed[OPTICS_PDF], 11), rows(parsed[OPTICS_PPTX], 11)
    assert pdf_rows == pptx_rows and len(pdf_rows) == 5
    assert pdf_rows[2] == ("제안 1 | 홀 센서 폐루프 제어에 자세별 중력 보상 전류를 더함 | 오차 2 μm 이내 3회 연속 | 신규",
                           ["구분", "구동 방식", "정착 판정", "비고"])
