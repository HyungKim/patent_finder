"""2단계 판단 어댑터 인터페이스 (스펙 8절).

이 프로젝트 자체의 인터페이스이며 특정 SDK의 호출 예제가 아니다.
현재 버전은 laya.mode=off만 지원하고 2단계 모델을 로드하지 않는다. shadow/assist/filter는 Phase 3 이후.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Assessment:
    status: str  # ok | error | timeout | not_run
    raw_scores: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    model_version: str | None = None
    question_version: str | None = None
    latency_ms: float | None = None
    error_code: str | None = None


class SecondStageAdapter(Protocol):
    def assess(self, target: str, context: str) -> Assessment:
        ...


class DisabledSecondStage:
    """laya.mode=off. 1차 판정을 그대로 최종 판정으로 쓴다."""

    def assess(self, target: str, context: str) -> Assessment:
        return Assessment(status="not_run")


def build_second_stage(mode: str) -> SecondStageAdapter:
    if mode != "off":
        raise NotImplementedError("2단계 모델(shadow/assist/filter)은 아직 구현되지 않았습니다 (Phase 3).")
    return DisabledSecondStage()
