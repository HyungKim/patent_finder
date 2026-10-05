"""라벨 snapshot으로 분류기를 학습하고 threshold 정책을 만든다 (스펙 7·10절).

- train partition의 확정 YES/NO만으로 학습한다. E5는 동결이다.
- threshold는 validation partition에서 고른다. test는 학습에도 threshold 선택에도 쓰지 않는다.
- 학습 결과는 registry에 '후보'로 등록될 뿐이며 운영 모델은 promote 전까지 바뀌지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .classifiers.bundle import ModelBundle, save_bundle
from .classifiers.registry import next_model_version, register_model
from .classifiers.seed import seed_features
from .classifiers.train import TrainingBlocked, fit_logistic, out_of_fold_scores
from .embeddings.features import feature_config, feature_config_hash
from .evaluation.reports import paragraph_units
from .feedback.snapshot import load_snapshot
from .policies.thresholds import CV_ESTIMATE, UNVALIDATED, VALIDATED, create_policy, select_threshold
from .runtime import utc_now
from .scoring import features_for
from .services import Services
from .versions import SEED_DATA_VERSION

# 스펙 7.2의 초기 수집 계획. 이 수에 못 미치면 '실험 모델'로 표시한다(품질 보장 기준은 아니다).
EXPERIMENTAL_MIN_LABELS = 200
EXPERIMENTAL_MIN_PER_CLASS = 50


def _labels(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.array([1 if row["label"] == "YES" else 0 for row in rows])


def _training_matrix(services: Services, features_cfg: dict[str, Any], rows: list[dict[str, Any]]):
    """사람 라벨 + (설정 시) seed 예문. 반환: X, y, weight, is_seed, seed 정보."""
    config = services.config
    features, _ = features_for(services, features_cfg, rows)
    labels = _labels(rows)
    weights = np.ones(len(rows))
    is_seed = np.zeros(len(rows), dtype=bool)
    seed_info = None
    if config.seed.enabled:
        seed_x, seed_y, examples, digest = seed_features(services, features_cfg["mode"])
        features = np.vstack([features, seed_x])
        labels = np.concatenate([labels, seed_y])
        weights = np.concatenate([weights, np.full(len(seed_y), config.seed.sample_weight)])
        is_seed = np.concatenate([is_seed, np.ones(len(seed_y), dtype=bool)])
        seed_info = {"seed_data_version": SEED_DATA_VERSION, "seed_data_sha256": digest,
                     "examples": len(examples), "sample_weight": config.seed.sample_weight}
    return features, labels, weights, is_seed, seed_info


def select_policy(services: Services, bundle: ModelBundle, snapshot: dict[str, Any], train: list[dict[str, Any]],
                  validation: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    """(선택 내역, 정책 상태). validation 양성이 부족하면 그룹 교차검증으로 추정한다."""
    config = services.config
    target = config.policy.recall_target
    if validation:
        from .scoring import score_segments

        scores, _ = score_segments(services, bundle, validation)
        units = paragraph_units(services, bundle, validation, scores)
        truth = np.array([unit["truth"] for unit in units])
        if int(truth.sum()) >= config.policy.min_validation_positives:
            selection = select_threshold(np.array([unit["score"] for unit in units]), truth, target)
            selection.update({"basis": "validation_partition", "level": "paragraph(max)",
                              "split_tag": snapshot["split_tag"]})
            return selection, VALIDATED
    rows = train + validation
    try:
        features, labels, weights, is_seed, _ = _training_matrix(services, bundle.feature_config, rows)
        groups = [row["document_family_id"] for row in rows] + ["seed"] * int(is_seed.sum())
        scores, folds = out_of_fold_scores(features, labels, groups, config.classifier, config.evaluation.cv_folds,
                                           sample_weight=weights, always_train=is_seed)
        valid = ~np.isnan(scores)
        selection = select_threshold(scores[valid], labels[valid], target)
        if selection is None:
            raise TrainingBlocked("교차검증 점수에 양성이 없습니다.")
        selection.update({
            "basis": "group_cross_validation", "level": "segment", "folds": folds,
            "note": f"validation 양성 문단이 {config.policy.min_validation_positives}개 미만이어서 "
                    "train+validation의 out-of-fold 점수로 추정함. test는 쓰지 않음.",
        })
        return selection, CV_ESTIMATE
    except TrainingBlocked as exc:
        return {"basis": "none", "threshold": None, "reason": str(exc)}, UNVALIDATED


def train_from_snapshot(services: Services, snapshot_path: Path) -> dict[str, Any]:
    config, database = services.config, services.database
    store = services.require_encoder()
    snapshot = load_snapshot(database, Path(snapshot_path))
    train = [row for row in snapshot["rows"] if row["partition"] == "train"]
    validation = [row for row in snapshot["rows"] if row["partition"] == "validation"]
    train_labels = _labels(train)
    yes, no = int(train_labels.sum()), int(len(train_labels) - train_labels.sum())
    if yes == 0 or no == 0:
        raise TrainingBlocked(
            f"train partition의 사람 라벨이 한 클래스뿐입니다 (YES {yes}, NO {no}). "
            "분류기 학습을 차단합니다. 라벨을 더 수집한 뒤 새 snapshot을 만드세요."
        )

    features_cfg = feature_config(config.features.mode, store.encoder.config_hash, config.segmentation)
    features, labels, weights, _is_seed, seed_info = _training_matrix(services, features_cfg, train)
    fit = fit_logistic(features, labels, config.classifier, weights)
    experimental = len(train) < EXPERIMENTAL_MIN_LABELS or min(yes, no) < EXPERIMENTAL_MIN_PER_CLASS

    model_version = next_model_version(database)
    training_run_id = f"train-{model_version.split('-')[1]}"
    bundle = ModelBundle(
        model_version=model_version, kind="trained", coef=fit.coef.tolist(), intercept=fit.intercept,
        feature_config=features_cfg, feature_config_hash=feature_config_hash(features_cfg),
        encoder_revision=store.encoder.encoder_revision, encoder_config_hash=store.encoder.config_hash,
        tokenizer_hash=store.encoder.tokenizer_hash, created_at=utc_now(),
        training={
            "fit": fit.info, "training_run_id": training_run_id, "snapshot_id": snapshot["snapshot_id"],
            "label_snapshot_hash": snapshot["label_snapshot_hash"],
            "split_manifest_hash": snapshot["split_manifest_hash"], "split_tag": snapshot["split_tag"],
            "train_families": sorted({row["document_family_id"] for row in train}),
            "train_text_hashes": sorted({row["text_hash"] for row in train}),
            "human_labels": {"train": len(train), "yes": yes, "no": no,
                             "implicit": sum(row["implicit"] for row in train),
                             "validation": len(validation)},
            "seed": seed_info, "experimental": experimental,
        },
    )
    selection, status = select_policy(services, bundle, snapshot, train, validation)
    path = config.artifacts_dir / "models" / f"{model_version}.json"
    artifact_sha = save_bundle(bundle, path)
    policy_version = create_policy(database, model_version, selection.get("threshold"), status, selection)
    register_model(database, bundle, path, artifact_sha, config.base_dir, training_run_id, policy_version)
    metrics = {"fit": fit.info, "policy": {"version": policy_version, "status": status, "selection": selection},
               "experimental": experimental, "human_labels": bundle.training["human_labels"]}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO training_runs (training_run_id, model_version, label_snapshot_hash, split_manifest_hash, "
            "feature_config_hash, random_seed, metrics_json, artifact_path, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'COMPLETED', ?)",
            (training_run_id, model_version, snapshot["label_snapshot_hash"], snapshot["split_manifest_hash"],
             bundle.feature_config_hash, config.classifier.random_seed,
             json.dumps(metrics, ensure_ascii=False), str(path), utc_now()),
        )
        connection.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('labels_at_last_training', ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (str(len(snapshot["rows"])), utc_now()),
        )
    return {"model_version": model_version, "artifact_path": str(path), "artifact_sha256": artifact_sha,
            "policy_version": policy_version, "policy_status": status, "threshold": selection.get("threshold"),
            "selection": selection, "experimental": experimental, "converged": fit.info["converged"],
            "human_labels": bundle.training["human_labels"], "seed": seed_info, "split_tag": snapshot["split_tag"]}


def compare_feature_modes(services: Services, snapshot_path: Path, modes: list[str],
                          c_values: list[float] | None = None) -> dict[str, Any]:
    """특징 구성(separate / target_only / composed)과 C를 그룹 교차검증으로 비교한다.

    train+validation만 쓰고 test는 쓰지 않는다. 한 번에 한 축씩 바꿔 비교하는 실험용이다.
    """
    import dataclasses

    from .evaluation import metrics as metric_tools

    config = services.config
    store = services.require_encoder()
    snapshot = load_snapshot(services.database, Path(snapshot_path))
    rows = [row for row in snapshot["rows"] if row["partition"] in ("train", "validation")]
    results = []
    for mode in modes:
        features_cfg = feature_config(mode, store.encoder.config_hash, config.segmentation)
        features, labels, weights, is_seed, _ = _training_matrix(services, features_cfg, rows)
        groups = [row["document_family_id"] for row in rows] + ["seed"] * int(is_seed.sum())
        for c_value in c_values or [config.classifier.C]:
            classifier = dataclasses.replace(config.classifier, C=c_value)
            scores, folds = out_of_fold_scores(features, labels, groups, classifier, config.evaluation.cv_folds,
                                               sample_weight=weights, always_train=is_seed)
            valid = ~np.isnan(scores)
            selection = select_threshold(scores[valid], labels[valid], config.policy.recall_target)
            results.append({
                "mode": mode, "C": c_value, "folds": folds, "n": int(valid.sum()),
                "n_positive": int(labels[valid].sum()),
                "average_precision": metric_tools.average_precision(labels[valid], scores[valid]),
                "recall_at_threshold": selection["recall"] if selection else None,
                "precision_at_threshold": selection["precision"] if selection else None,
                "review_ratio_at_threshold": selection["review_ratio"] if selection else None,
            })
    return {"snapshot_id": snapshot["snapshot_id"], "recall_target": config.policy.recall_target,
            "basis": "group cross-validation (out-of-fold), train+validation only", "results": results}
