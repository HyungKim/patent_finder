"""정규화된 텍스트 위치를 원문 위치로 되돌리는 offset map (스펙 5.1).

offset 단위는 Python 문자열 인덱스(유니코드 코드 포인트)이다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OffsetMap:
    starts: list[int]  # 정규화 문자 i의 원문 시작 위치
    ends: list[int]  # 정규화 문자 i의 원문 끝 위치(제외)
    original_length: int

    def __len__(self) -> int:
        return len(self.starts)

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        """정규화 구간 [start, end)에 대응하는 원문 구간."""
        if not 0 <= start <= end <= len(self.starts):
            raise IndexError(f"정규화 구간이 범위를 벗어남: [{start}, {end}) / {len(self.starts)}")
        if start == end:
            position = self.starts[start] if start < len(self.starts) else self.original_length
            return position, position
        return self.starts[start], self.ends[end - 1]


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """겹치거나 맞닿은 구간을 합친다 (중첩 segment의 중복 표시 병합에 사용)."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]
