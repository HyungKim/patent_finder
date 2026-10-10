"""합성 자료 정답표 대조 도구(tools/check_expected.py)의 짝짓기 규칙 시험."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from check_expected import compare, home_segments, laya_counts, squeeze  # noqa: E402

DESCRIPTION = "온도 추정기 출력이 5도 이상 변하면 홀 센서 보정 블록이 오프셋 테이블을 다시 읽는다."


def _segment(segment_id: str, text: str, candidate: bool, score: float = 0.6) -> dict:
    return {"segment_id": segment_id, "original_text": text, "stage1_score": score,
            "final_decision": "CANDIDATE" if candidate else "NOT_CANDIDATE"}


def _item(kind: str, label: str, text: str, slide: int = 1, scope: str = "both") -> dict:
    return {"document": "report", "slide": str(slide), "kind": kind, "label": label, "scope": scope, "text": text}


def _counts(result: dict, fmt: str = "pptx") -> dict:
    counter = result["stats"][("report", fmt)]
    return {key: counter[key] for key in ("tp", "fn", "fp", "tn", "shared", "unmatched")}


def test_item_is_placed_on_the_segment_it_covers_best():
    rows = [_segment("box", "온도 추정기", False), _segment("desc", DESCRIPTION, True)]
    assert [row["segment_id"] for row in home_segments(squeeze("온도 추정기"), rows)] == ["box"]
    assert [row["segment_id"] for row in home_segments(squeeze(DESCRIPTION), rows)] == ["desc"]
    # 긴 항목이 두 구간으로 나뉘면 두 조각이 모두 그 항목의 구간이다
    halves = [_segment("a", DESCRIPTION[:28], False), _segment("b", DESCRIPTION[28:], True)]
    assert [row["segment_id"] for row in home_segments(squeeze(DESCRIPTION), halves)] == ["a", "b"]
    assert home_segments(squeeze("없는 글"), rows) == []


def test_block_name_is_not_a_false_mark_just_because_a_marked_sentence_mentions_it():
    # 블록 이름 구간은 후보가 아니고, 그 이름이 들어간 설명 문장만 후보다
    segments = {("report", "pptx", 1): [_segment("box", "온도 추정기", False), _segment("desc", DESCRIPTION, True)]}
    expected = [_item("diagram_box", "NO", "온도 추정기"), _item("description", "YES", DESCRIPTION)]
    assert _counts(compare(segments, expected)) == {"tp": 1, "fn": 0, "fp": 0, "tn": 1, "shared": 0, "unmatched": 0}


def test_marked_line_of_block_names_counts_each_name_and_laya_can_remove_it():
    # PDF에서는 블록 이름들이 한 줄로 합쳐져 한 구간이 된다. 그 구간이 후보면 이름마다 잘못 표시다
    segments = {("report", "pdf", 1): [_segment("line", "온도 추정기 홀 센서 보정 코일 구동기", True),
                                        _segment("desc", DESCRIPTION, True)]}
    expected = [_item("diagram_box", "NO", name) for name in ("온도 추정기", "홀 센서 보정", "코일 구동기")]
    expected.append(_item("description", "YES", DESCRIPTION))
    result = compare(segments, expected, {"line": 0.1, "desc": 0.9})
    assert _counts(result, "pdf") == {"tp": 1, "fn": 0, "fp": 3, "tn": 0, "shared": 0, "unmatched": 0}
    assert dict(laya_counts(result["laya_items"], 0.0)["pdf"]) == {"fp": 3, "tp": 1}
    assert dict(laya_counts(result["laya_items"], 0.5)["pdf"]) == {"tn": 3, "tp": 1}
    assert dict(laya_counts(result["laya_items"], 0.95)["pdf"]) == {"tn": 3, "fn": 1}
    # Laya가 판단하지 못한 구간은 어떤 기준에서도 빼지 않는다
    unknown = compare(segments, expected, {"desc": 0.9})
    assert dict(laya_counts(unknown["laya_items"], 0.5)["pdf"]) == {"fp": 3, "tp": 1}


def test_no_item_sharing_a_marked_segment_with_a_real_candidate_is_counted_separately():
    group = "배경: 기존 방식은 편차가 크다\n편차가 3도를 넘으면 밸브 개도를 편차에 비례해 늘린다"
    segments = {("report", "pptx", 2): [_segment("group", group, True)]}
    expected = [_item("bullet", "NO", "배경: 기존 방식은 편차가 크다", slide=2),
                _item("bullet", "YES", "편차가 3도를 넘으면 밸브 개도를 편차에 비례해 늘린다", slide=2)]
    assert _counts(compare(segments, expected)) == {"tp": 1, "fn": 0, "fp": 0, "tn": 1, "shared": 1, "unmatched": 0}


def test_missed_unextracted_and_out_of_scope_items():
    segments = {("report", "pptx", 1): [_segment("s1", "측정값이 기준을 넘으면 출력을 낮춘다", False, 0.4)],
                ("report", "pdf", 1): [_segment("p1", "측정값이 기준을 넘으면 출력을 낮춘다", True)]}
    expected = [_item("bullet", "YES", "측정값이 기준을 넘으면 출력을 낮춘다"),
                _item("bullet", "YES", "그림 안에만 있는 문장이라 추출되지 않는다"),
                _item("bullet", "YES", "숨긴 슬라이드의 문장", scope="pptx_hidden"),
                _item("bullet", "YES", "발표자 노트의 문장", scope="pptx_notes"),
                _item("bullet", "HOLD", "판단이 갈리는 문장"),
                {**_item("bullet", "YES", "다른 문서의 문장"), "document": "other"}]
    result = compare(segments, expected)
    assert _counts(result, "pptx") == {"tp": 0, "fn": 3, "fp": 0, "tn": 0, "shared": 0, "unmatched": 2}
    assert _counts(result, "pdf") == {"tp": 1, "fn": 1, "fp": 0, "tn": 0, "shared": 0, "unmatched": 1}
    assert sorted(reason for _doc, fmt, _item_, reason in result["missed"] if fmt == "pptx") == [
        "점수 0.40", "추출 안 됨", "추출 안 됨"]
