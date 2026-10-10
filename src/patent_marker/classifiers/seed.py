"""합성 seed 분류기 (변경안 A3).

저장소에 포함된 비기밀 합성 예문으로 classifier-0000을 만들어 라벨이 없는 첫날에도
'임시 후보'를 표시한다. threshold는 합성 예문의 교차검증으로만 정한 것이므로
SEED_UNVALIDATED 상태이며, 실제 문서에서의 Recall은 측정되지 않았다.
"""
from __future__ import annotations

import json
from importlib import resources
from typing import Any

import numpy as np

from ..embeddings.features import FeatureInput, build_features, feature_config, feature_config_hash
from ..policies.thresholds import SEED_UNVALIDATED, create_policy, select_threshold
from ..runtime import sha256_text, utc_now
from ..segmentation.normalization import normalize_text
from ..services import Services
from ..versions import SEED_DATA_VERSION, segmentation_version
from .bundle import ModelBundle, compatibility_problems, save_bundle
from .registry import (SEED_MODEL_VERSION, STAGE_SEED, get_active, get_model, load_registered_bundle,
                       next_model_version, register_model, set_active)
from .train import fit_logistic, out_of_fold_scores

_SEED_FILE = "seed_v1.jsonl"


def load_seed_examples() -> tuple[list[dict[str, Any]], str]:
    """(예문 목록, 파일 sha256)."""
    raw = (resources.files("patent_marker") / "seed_data" / _SEED_FILE).read_text(encoding="utf-8")
    examples = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for example in examples:
        example["text"] = normalize_text(example["text"])
    return examples, sha256_text(raw)


def seed_features(services: Services, mode: str) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], str]:
    """seed 예문의 특징 행렬과 0/1 라벨. 예문에는 문맥이 없다."""
    examples, digest = load_seed_examples()
    inputs = [FeatureInput(target=example["text"]) for example in examples]
    features, _ = build_features(inputs, services.require_encoder(), mode, services.tokenizer,
                                 services.config.segmentation)
    labels = np.array([1 if example["label"] == "YES" else 0 for example in examples])
    return features, labels, examples, digest


def _compatible_seed(services: Services) -> dict[str, Any] | None:
    """현재 인코더와 호환되는 가장 최근 seed 모델."""
    encoder_hash = services.require_encoder().encoder.config_hash
    for row in services.database.query(
            "SELECT model_version FROM model_registry WHERE kind = 'seed' ORDER BY created_at DESC, model_version DESC"):
        bundle = load_registered_bundle(services.database, row["model_version"], services.config.base_dir)
        if not compatibility_problems(bundle, encoder_hash, segmentation_version(services.config.segmentation.unit)):
            return get_model(services.database, row["model_version"])
    return None


def ensure_seed_model(services: Services) -> dict[str, Any] | None:
    """호환되는 seed 모델이 없으면 만들고, 운영 모델이 없으면 SEED 단계로 지정한다.

    인코더나 전처리가 바뀌어 기존 seed가 호환되지 않으면 새 버전으로 다시 만든다.
    운영 중인 모델이 호환되지 않는 seed일 때만 새 seed로 교체한다(학습된 모델은 자동으로 바꾸지 않는다).
    """
    config, database = services.config, services.database
    if not config.seed.enabled:
        return None
    existing = _compatible_seed(services)
    if existing is None:
        store = services.require_encoder()
        mode = config.features.mode
        features, labels, examples, digest = seed_features(services, mode)
        fit = fit_logistic(features, labels, config.classifier)
        groups = [example["id"] for example in examples]
        scores, folds = out_of_fold_scores(features, labels, groups, config.classifier, config.evaluation.cv_folds)
        valid = ~np.isnan(scores)
        selection = select_threshold(scores[valid], labels[valid], config.policy.recall_target) or {}
        selection.update({
            "basis": "seed_cross_validation", "folds": folds,
            "note": "합성 seed 예문의 교차검증으로 정한 임시 threshold. 실제 문서에서 검증되지 않음.",
        })
        version = SEED_MODEL_VERSION if get_model(database, SEED_MODEL_VERSION) is None else next_model_version(database)
        features_cfg = feature_config(mode, store.encoder.config_hash, config.segmentation)
        bundle = ModelBundle(
            segmentation_version=segmentation_version(config.segmentation.unit),
            model_version=version, kind="seed", coef=fit.coef.tolist(), intercept=fit.intercept,
            feature_config=features_cfg, feature_config_hash=feature_config_hash(features_cfg),
            encoder_revision=store.encoder.encoder_revision, encoder_config_hash=store.encoder.config_hash,
            tokenizer_hash=store.encoder.tokenizer_hash, created_at=utc_now(),
            training={"fit": fit.info, "seed_data_version": SEED_DATA_VERSION, "seed_data_sha256": digest,
                      "human_labels": 0},
        )
        path = config.artifacts_dir / "models" / f"{version}.json"
        artifact_sha = save_bundle(bundle, path)
        policy_version = create_policy(database, version, selection.get("threshold"), SEED_UNVALIDATED, selection)
        register_model(database, bundle, path, artifact_sha, config.base_dir, None, policy_version)
        existing = get_model(database, version)
    active = get_active(database)
    replace_stale_seed = False
    if active and active["model_version"] != existing["model_version"]:
        current = get_model(database, active["model_version"])
        if current and current["kind"] == "seed":
            bundle = load_registered_bundle(database, active["model_version"], config.base_dir)
            replace_stale_seed = bool(compatibility_problems(
                bundle, services.require_encoder().encoder.config_hash,
                segmentation_version(services.config.segmentation.unit)))
    if active is None or replace_stale_seed:
        reason = ("운영 모델이 없어 합성 seed 분류기를 임시로 사용" if active is None
                  else "인코더·전처리가 바뀌어 seed 분류기를 다시 만듦")
        set_active(database, existing["model_version"], existing["policy_version"], STAGE_SEED, "SEED_INIT", reason=reason)
    return existing
