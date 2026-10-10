"""segment 점수 계산과 운영 bundle 로드. 분석·평가·재현 검증이 같은 경로를 쓴다."""
from __future__ import annotations

import dataclasses
from typing import Any

import numpy as np

from .classifiers.bundle import ModelBundle, compatibility_problems
from .classifiers.registry import get_active, load_registered_bundle
from .config import SegmentationConfig
from .embeddings.features import FeatureInput, build_features
from .policies.thresholds import get_policy
from .services import Services
from .versions import segmentation_version


class IncompatibleBundle(RuntimeError):
    pass


def segmentation_for(bundle_feature_config: dict[str, Any], current: SegmentationConfig) -> SegmentationConfig:
    """composed 모드에서는 학습 때의 문맥 예산을 그대로 쓴다."""
    composed = bundle_feature_config.get("composed")
    return dataclasses.replace(current, **composed) if composed else current


def feature_inputs(services: Services, segments: list[dict[str, Any]]) -> list[FeatureInput]:
    """segment 행(normalized_text, context_segment_ids)에서 특징 입력을 만든다. 문맥 텍스트는 DB에서 찾는다."""
    needed: set[str] = set()
    for segment in segments:
        needed.update(value for value in segment["context_segment_ids"].values() if value)
    known = {segment["segment_id"]: segment["normalized_text"] for segment in segments}
    missing = sorted(needed - set(known))
    for start in range(0, len(missing), 500):
        chunk = missing[start: start + 500]
        rows = services.database.query(
            f"SELECT segment_id, normalized_text FROM segments WHERE segment_id IN ({','.join('?' * len(chunk))})", chunk
        )
        known.update({row["segment_id"]: row["normalized_text"] for row in rows})
    inputs = []
    for segment in segments:
        context = segment["context_segment_ids"]
        inputs.append(FeatureInput(
            target=segment["normalized_text"],
            title=known.get(context.get("title")) if context.get("title") else None,
            prev=known.get(context.get("prev")) if context.get("prev") else None,
            next=known.get(context.get("next")) if context.get("next") else None,
        ))
    return inputs


def features_for(services: Services, feature_config: dict[str, Any],
                 segments: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    store = services.require_encoder()
    return build_features(
        feature_inputs(services, segments), store, feature_config["mode"], services.tokenizer,
        segmentation_for(feature_config, services.config.segmentation),
    )


def score_segments(services: Services, bundle: ModelBundle,
                   segments: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
    """후보 점수와 embedding_id."""
    if not segments:
        return np.zeros(0), []
    features, embedding_ids = features_for(services, bundle.feature_config, segments)
    return bundle.model().scores(features), embedding_ids


def ensure_compatible(services: Services, bundle: ModelBundle) -> None:
    problems = compatibility_problems(bundle, services.require_encoder().encoder.config_hash,
                                      segmentation_version(services.config.segmentation.unit))
    if problems:
        raise IncompatibleBundle(
            f"{bundle.model_version}은(는) 현재 환경과 호환되지 않습니다: " + " / ".join(problems)
        )


def load_active(services: Services) -> dict[str, Any] | None:
    """운영 중인 (bundle, 정책, 단계). 운영 모델이 없으면 None."""
    active = get_active(services.database)
    if not active:
        return None
    bundle = load_registered_bundle(services.database, active["model_version"], services.config.base_dir)
    ensure_compatible(services, bundle)
    return {"bundle": bundle, "policy": get_policy(services.database, active.get("policy_version")),
            "stage": active.get("stage"), "model_version": active["model_version"],
            "policy_version": active.get("policy_version")}


def load_for_run(services: Services, model_version: str, policy_version: str | None,
                 stage: str | None) -> dict[str, Any]:
    """이미 시작된 run이 기록해 둔 버전을 그대로 로드한다 (진행 중 run은 버전이 바뀌지 않는다)."""
    bundle = load_registered_bundle(services.database, model_version, services.config.base_dir)
    ensure_compatible(services, bundle)
    return {"bundle": bundle, "policy": get_policy(services.database, policy_version), "stage": stage,
            "model_version": model_version, "policy_version": policy_version}
