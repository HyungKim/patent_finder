"""규칙 기반 문장 분할 (스펙 5.2).

한국어 종결, 목록 표기, 영어 약어, 소수점에 대한 회귀 사례를 tests/unit/test_sentences.py에 둔다.
문장부호 없이 끝나는 문장(개조식)은 나누지 않으며, 너무 길면 분할기가 토큰 기준으로 나눈다.
"""
from __future__ import annotations

import re

_TERMINATORS = ".!?。！？…"
_CLOSERS = "\"')]}”’」』》〉"
_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "cf", "al", "fig", "figs", "eq", "eqs", "no", "nos", "ref", "refs",
    "sec", "ch", "vol", "pp", "approx", "dr", "mr", "mrs", "ms", "prof", "inc", "ltd", "co", "corp",
    "st", "jr", "sr", "ver", "rev", "max", "min", "avg", "temp", "tel", "dept",
}
_TOKEN_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*|\d+|[가-힣]|[ivxIVX]+)$")
_ENUMERATOR = re.compile(r"^\(?(\d{1,3}|[A-Za-z]|[가-힣]|[ivxIVX]{1,4})$")


def _is_boundary(text: str, sentence_start: int, index: int, after: int) -> bool:
    """text[index]의 종결 부호 뒤(after 위치의 공백)에서 문장을 끊을지 판단한다."""
    ch = text[index]
    following = text[after + 1: after + 2] if after < len(text) else ""
    if ch != ".":
        return True
    before = text[sentence_start:index]
    match = _TOKEN_BEFORE.search(before)
    token = match.group(1) if match else ""
    # "1. 개요", "가. 목적": 문장 머리의 번호 표식
    if token and _ENUMERATOR.match(before.strip()):
        return False
    if token.lower().strip(".") in _ABBREVIATIONS or token.lower() in _ABBREVIATIONS:
        return False
    # 약어 뒤에는 보통 소문자가 이어진다 ("approx. value")
    if following and following.isascii() and following.islower():
        return False
    # "J. Smith" 같은 머리글자
    if len(token) == 1 and token.isascii() and token.isupper() and following.isascii() and following.isupper():
        preceded = before[: match.start(1)] if match else before
        if not preceded or preceded.endswith(" "):
            return False
    return True


def split_sentences(text: str, forced_breaks: set[int] | frozenset[int] = frozenset()) -> list[tuple[int, int]]:
    """정규화된 텍스트를 문장 구간으로 나눈다.

    forced_breaks: 강제 줄바꿈에서 온 공백의 위치. 그 공백에서는 항상 끊는다.
    반환 구간은 앞뒤 공백을 포함하지 않는다.
    """
    spans: list[tuple[int, int]] = []
    length = len(text)
    start = 0

    def close(end: int) -> None:
        nonlocal start
        a, b = start, end
        while a < b and text[a] == " ":
            a += 1
        while b > a and text[b - 1] == " ":
            b -= 1
        if b > a:
            spans.append((a, b))
        start = end

    index = 0
    while index < length:
        ch = text[index]
        if ch == " " and index in forced_breaks:
            close(index)
            index += 1
            continue
        if ch in _TERMINATORS:
            end = index + 1
            while end < length and text[end] in _TERMINATORS:
                end += 1
            while end < length and text[end] in _CLOSERS:
                end += 1
            if end >= length:
                break
            if text[end] == " " and _is_boundary(text, start, index, end):
                close(end)
                index = end + 1
                continue
            index = end
            continue
        index += 1
    close(length)
    return spans
