"""정규화·offset·문장 분할·segment 분할 회귀 시험 (스펙 5절, 17.1)."""
from __future__ import annotations

import unicodedata

import pytest

from patent_marker.config import SegmentationConfig
from patent_marker.parsers.base import Block
from patent_marker.segmentation.context import assign_context, compose_input
from patent_marker.segmentation.normalization import normalize
from patent_marker.segmentation.paragraphs import build_paragraphs
from patent_marker.segmentation.segmenter import SegmentationError, segment_paragraph
from patent_marker.segmentation.sentences import split_sentences


# ---------------------------------------------------------------- 정규화
def test_normalize_preserves_case_units_operators_and_negation():
    text = "전류가 3.5 mA < 4 MA 이면 ≥ 조건을 적용하지 않는다. Radar confidence"
    normalized, _ = normalize(text)
    assert normalized == text  # 소문자화·단위 치환·연산자 변경 없음
    assert "mA" in normalized and "MA" in normalized and "<" in normalized and "≥" in normalized
    assert "적용하지 않는다" in normalized


def test_normalize_collapses_whitespace_and_maps_offsets():
    text = "  가중치\t\t조절 \n\n 방법​을  적용  "
    normalized, omap = normalize(text)
    assert normalized == "가중치 조절 방법을 적용"
    start = normalized.index("조절")
    o_start, o_end = omap.to_original(start, start + 2)
    assert text[o_start:o_end] == "조절"
    # 공백 하나는 원문의 공백 묶음 전체에 대응한다
    space = normalized.index(" ")
    a, b = omap.to_original(space, space + 1)
    assert text[a:b] == "\t\t"


def test_normalize_nfd_hangul_maps_to_original_jamo():
    composed = "임계값 조절"
    decomposed = unicodedata.normalize("NFD", composed)
    normalized, omap = normalize(decomposed)
    assert normalized == composed and len(decomposed) > len(composed)
    start, end = omap.to_original(0, 3)
    assert unicodedata.normalize("NFC", decomposed[start:end]) == "임계값"
    start, end = omap.to_original(4, 6)
    assert unicodedata.normalize("NFC", decomposed[start:end]) == "조절"


def test_offsets_do_not_rely_on_string_search_for_repeated_text():
    text = "가중치 조절. 가중치 조절. 가중치 조절."
    normalized, omap = normalize(text)
    spans = split_sentences(normalized)
    originals = [omap.to_original(a, b) for a, b in spans]
    assert originals == [(0, 7), (8, 15), (16, 23)]
    assert all(text[a:b] == "가중치 조절." for a, b in originals)


# ---------------------------------------------------------------- 문장 분할
@pytest.mark.parametrize("text, expected", [
    ("임계값을 갱신한다. 이후 가중치를 조절한다.", ["임계값을 갱신한다.", "이후 가중치를 조절한다."]),
    ("전류는 3.5 mA 이하이다. 전압은 1.2 V이다.", ["전류는 3.5 mA 이하이다.", "전압은 1.2 V이다."]),
    ("본 방식은 e.g. 필터 계수를 바꾼다. 결과는 Fig. 3 참조.", ["본 방식은 e.g. 필터 계수를 바꾼다.", "결과는 Fig. 3 참조."]),
    ("1. 개요", ["1. 개요"]),
    ("가. 목적은 다음과 같다. 나. 범위는 제한한다.", ["가. 목적은 다음과 같다.", "나. 범위는 제한한다."]),
    ("A. Smith et al. proposed it. It works!", ["A. Smith et al. proposed it.", "It works!"]),
    ("버전은 v1.2.3이다. No. 5 장비를 쓴다.", ["버전은 v1.2.3이다.", "No. 5 장비를 쓴다."]),
    ("값이 커진다… 그래서 낮춘다.", ["값이 커진다…", "그래서 낮춘다."]),
    ("그는 \"된다.\" 라고 했다. 끝.", ["그는 \"된다.\"", "라고 했다.", "끝."]),
    ("개조식 문장은 문장부호가 없음 그래서 나누지 않음", ["개조식 문장은 문장부호가 없음 그래서 나누지 않음"]),
    ("approx. value is fine. Next one.", ["approx. value is fine.", "Next one."]),
])
def test_sentence_split_regressions(text, expected):
    assert [text[a:b] for a, b in split_sentences(text)] == expected


def test_forced_breaks_split_without_punctuation():
    text = "첫 불릿 내용 둘째 불릿 내용"
    forced = {text.index(" 둘째")}
    assert [text[a:b] for a, b in split_sentences(text, forced)] == ["첫 불릿 내용", "둘째 불릿 내용"]


# ---------------------------------------------------------------- 논리 문단
def _item(text, level=0, container="c1", kind="list_item"):
    return Block(kind=kind, text=text, locator={"p": text}, unit=1, container=container, level=level, section_key="s1")


def test_bullets_merge_only_when_structurally_connected(tokenizer):
    config = SegmentationConfig()
    blocks = [
        _item("상위 항목"), _item("하위 하나", 1), _item("하위 둘", 1),
        _item("같은 수준 이웃"), _item("다른 상자 항목", container="c2"),
    ]
    paragraphs = build_paragraphs(blocks, tokenizer, config)
    assert [p.kind for p in paragraphs] == ["list_group", "list_item", "list_item"]
    group = paragraphs[0]
    assert group.original_text == "상위 항목\n하위 하나\n하위 둘"
    assert [(part["start"], part["end"]) for part in group.parts] == [(0, 5), (6, 11), (12, 16)]
    assert group.hard_breaks == {5, 11}


def test_lead_in_colon_groups_following_list(tokenizer):
    blocks = [_item("적용 조건은 다음과 같다:", kind="paragraph", container=None), _item("조건 하나"), _item("조건 둘")]
    paragraphs = build_paragraphs(blocks, tokenizer, SegmentationConfig())
    assert len(paragraphs) == 1 and paragraphs[0].kind == "list_group"


def test_group_respects_token_budget(tokenizer):
    config = SegmentationConfig(target_tokens=10, short_item_tokens=6, overlap_tokens=2)
    blocks = [_item("상위 항목"), _item("하위 하나 내용", 1), _item("하위 둘 내용 더 길다", 1), _item("하위 셋", 1)]
    paragraphs = build_paragraphs(blocks, tokenizer, config)
    assert paragraphs[0].kind == "list_group" and len(paragraphs[0].parts) == 2
    assert len(paragraphs) == 3  # 예산을 넘는 항목부터는 따로 둔다


# ---------------------------------------------------------------- segment 분할
def _paragraph(text, tokenizer, **kwargs):
    block = Block(kind="paragraph", text=text, locator={}, section_key="s", **kwargs)
    return build_paragraphs([block], tokenizer, SegmentationConfig())[0]


def _covered(text, drafts):
    covered = set()
    for draft in drafts:
        for start, end in draft.spans:
            covered.update(range(start, end))
    return {i for i, ch in enumerate(text) if not ch.isspace()} <= covered


def test_long_paragraph_is_split_at_sentences_without_dropping_text(tokenizer):
    sentence = "레이더 신뢰도가 낮으면 카메라 가중치를 높인다."
    text = " ".join([sentence] * 30)
    config = SegmentationConfig(target_tokens=40, overlap_tokens=8)
    drafts = segment_paragraph(_paragraph(text, tokenizer), tokenizer, config)
    assert len(drafts) > 1
    assert all(draft.token_count <= 40 for draft in drafts)
    assert _covered(text, drafts)  # 조용히 잘린 내용이 없다
    for draft in drafts:  # 구간은 원문 위치 그대로이며 문장 경계에서 끊긴다
        start, end = draft.spans[0]
        assert text[start:end] == draft.text
        assert draft.text.endswith("높인다.")


def test_single_long_sentence_uses_token_windows_with_overlap(tokenizer):
    text = "가" * 400  # 문장부호 없는 긴 문장 → 4자 토큰 100개
    config = SegmentationConfig(target_tokens=30, overlap_tokens=6)
    drafts = segment_paragraph(_paragraph(text, tokenizer), tokenizer, config)
    assert len(drafts) >= 4 and _covered(text, drafts)
    assert all("token_split" in draft.flags for draft in drafts)
    assert "overlap" in drafts[1].flags
    first_end, second_start = drafts[0].spans[0][1], drafts[1].spans[0][0]
    assert second_start < first_end  # 겹침이 있다
    assert (first_end - second_start) == 6 * 4


def test_segment_over_model_limit_raises_instead_of_truncating(tokenizer):
    config = SegmentationConfig(max_input_tokens=20, target_tokens=19, overlap_tokens=2)
    with pytest.raises(SegmentationError):
        segment_paragraph(_paragraph("가나다라 " * 40, tokenizer), tokenizer, config)


def test_table_row_builds_header_labelled_input_and_keeps_cell_positions(tokenizer):
    text = "전처리 | 블록별 분산으로 필터 강도를 선택 | 신규"
    cells = [{"col": 0, "start": 0, "end": 3, "header": "모듈"},
             {"col": 1, "start": 6, "end": 24, "header": "처리 방식"},
             {"col": 2, "start": 27, "end": 29, "header": "비고"}]
    block = Block(kind="table_row", text=text, locator={}, cells=cells, section_key="s")
    paragraph = build_paragraphs([block], tokenizer, SegmentationConfig())[0]
    drafts = segment_paragraph(paragraph, tokenizer, SegmentationConfig())
    assert len(drafts) == 1
    assert drafts[0].text == "모듈: 전처리 | 처리 방식: 블록별 분산으로 필터 강도를 선택 | 비고: 신규"
    assert [text[a:b] for a, b in drafts[0].spans] == ["전처리", "블록별 분산으로 필터 강도를 선택", "신규"]


def test_long_table_row_is_split_by_cell_with_row_label(tokenizer):
    long_cell = "임계값을 넘으면 출력을 낮춘다. " * 12
    text = f"전처리 | {long_cell.strip()} | 신규"
    end = 6 + len(long_cell.strip())
    cells = [{"col": 0, "start": 0, "end": 3, "header": "모듈"},
             {"col": 1, "start": 6, "end": end, "header": "방식"},
             {"col": 2, "start": end + 3, "end": end + 5, "header": "비고"}]
    block = Block(kind="table_row", text=text, locator={}, cells=cells, section_key="s")
    config = SegmentationConfig(target_tokens=40, overlap_tokens=4)
    paragraph = build_paragraphs([block], tokenizer, config)[0]
    drafts = segment_paragraph(paragraph, tokenizer, config)
    assert len(drafts) > 2 and all("row_split" in draft.flags for draft in drafts)
    assert all(draft.text.startswith("전처리 | ") for draft in drafts)
    assert all(draft.token_count <= 40 for draft in drafts)


# ---------------------------------------------------------------- 문맥
def test_context_stays_inside_section_and_title_is_not_duplicated():
    refs = assign_context(["s1", "s1", "s1", "s2", "s2"], [True, False, False, False, False])
    assert (refs[0].prev, refs[0].next, refs[0].title) == (None, 1, None)
    assert (refs[1].prev, refs[1].next, refs[1].title) == (0, 2, None)  # 제목이 곧 이전 구간
    assert (refs[2].prev, refs[2].next, refs[2].title) == (1, None, 0)
    assert (refs[3].prev, refs[3].next, refs[3].title) == (None, 4, None)  # 섹션 경계를 넘지 않는다


def test_composed_input_trims_context_before_target(tokenizer):
    config = SegmentationConfig(max_input_tokens=40, target_tokens=20, context_tokens=16, title_tokens=4)
    target = "대상 본문 " * 8
    long_context = "문맥 내용 " * 60
    composed = compose_input(target.strip(), "아주 긴 제목 " * 10, long_context, long_context, tokenizer, config)
    assert f"[판정 대상] {target.strip()}" in composed  # 대상은 온전히 남는다
    assert tokenizer.count(composed) <= 40 - tokenizer.overhead - 2
    with pytest.raises(ValueError):
        compose_input("대상 " * 80, None, None, None, tokenizer, config)
