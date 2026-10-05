"""모델 입력용 정규화: NFC와 불필요한 공백 정리만 한다 (스펙 5.1).

대소문자, 단위, 숫자, 비교 연산자, 부정 표현, 약어는 바꾸지 않는다.
번역·불용어 제거·형태소 원형화·소문자화를 하지 않는다.
"""
from __future__ import annotations

import unicodedata

from .offsets import OffsetMap

# 보이지 않고 의미가 없는 문자: zero-width space, BOM
_REMOVED = {"​", "﻿"}


def _nfc_clusters(text: str) -> list[tuple[str, int, int]]:
    """NFC 결합 단위별로 (정규화 문자열, 원문 시작, 원문 끝)을 만든다."""
    if unicodedata.is_normalized("NFC", text):
        return [(ch, index, index + 1) for index, ch in enumerate(text)]
    nfc = unicodedata.normalize
    clusters: list[tuple[str, int, int]] = []
    buffer = ""
    start = 0
    for index, ch in enumerate(text):
        if not buffer:
            buffer, start = ch, index
            continue
        # 앞 묶음과 결합하지 않는 문자에서만 끊는다 (분해된 한글 자모, 결합 문자 처리).
        if unicodedata.combining(ch) == 0 and nfc("NFC", buffer + ch) == nfc("NFC", buffer) + nfc("NFC", ch):
            clusters.append((nfc("NFC", buffer), start, index))
            buffer, start = ch, index
        else:
            buffer += ch
    if buffer:
        clusters.append((nfc("NFC", buffer), start, len(text)))
    return clusters


def normalize(text: str) -> tuple[str, OffsetMap]:
    """정규화 텍스트와 offset map을 돌려준다. 연속 공백은 공백 하나로, 양끝 공백은 제거한다."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pending: list[int] | None = None
    for normalized, start, end in _nfc_clusters(text):
        for ch in normalized:
            if ch in _REMOVED:
                continue
            if ch.isspace():
                if out:
                    if pending is None:
                        pending = [start, end]
                    else:
                        pending[1] = end
                continue
            if pending is not None:
                out.append(" ")
                starts.append(pending[0])
                ends.append(pending[1])
                pending = None
            out.append(ch)
            starts.append(start)
            ends.append(end)
    return "".join(out), OffsetMap(starts, ends, len(text))


def normalize_text(text: str) -> str:
    return normalize(text)[0]
