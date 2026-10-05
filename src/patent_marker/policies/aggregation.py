"""문단 단위 집계 (스펙 5.2, 7.3).

- 문단 표시 여부: 하위 segment 점수의 max.
- 문단 정답: 하위 segment 중 YES가 하나라도 있으면 YES, 모든 segment가 NO이면 NO,
  그 외(일부만 검토, HOLD 포함)는 미정으로 평가에서 제외한다. 문단 수준 라벨이 있으면 그것을 따른다.
"""
from __future__ import annotations

from typing import Iterable


def paragraph_score(segment_scores: Iterable[float | None]) -> float | None:
    values = [score for score in segment_scores if score is not None]
    return max(values) if values else None


def paragraph_truth(segment_labels: list[str | None], paragraph_label: str | None = None) -> str | None:
    """segment_labels: 문단의 모든 segment에 대한 확정 라벨(없으면 None)."""
    if "YES" in segment_labels or paragraph_label == "YES":
        return "YES"
    if paragraph_label == "NO":
        return "NO"
    if segment_labels and all(label == "NO" for label in segment_labels):
        return "NO"
    return None
