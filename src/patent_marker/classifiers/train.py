"""Logistic Regression 학습과 그룹 교차검증 (스펙 7.1, 7.2).

E5는 동결이고 여기서는 선형 분류기만 배치 학습한다. 클릭마다 온라인 갱신하지 않는다.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import ClassifierConfig


class TrainingBlocked(RuntimeError):
    """학습을 진행할 수 없는 데이터 상태 (예: 한 클래스만 있음)."""


@dataclass
class FitResult:
    coef: np.ndarray
    intercept: float
    info: dict[str, Any]


def fit_logistic(features: np.ndarray, labels: np.ndarray, config: ClassifierConfig,
                 sample_weight: np.ndarray | None = None) -> FitResult:
    """labels는 0/1. 양성 열은 classes_로 확인한다."""
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression

    labels = np.asarray(labels).astype(int)
    if len(set(labels.tolist())) < 2:
        raise TrainingBlocked("한 클래스의 라벨만 있어 분류기를 학습할 수 없습니다. YES와 NO가 모두 필요합니다.")
    model = LogisticRegression(
        C=config.C, class_weight=config.class_weight, max_iter=config.max_iter, random_state=config.random_seed,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model.fit(features.astype(np.float64), labels, sample_weight=sample_weight)
    converged = not any(issubclass(item.category, ConvergenceWarning) for item in caught)
    classes = list(model.classes_)
    if classes != [0, 1]:
        raise TrainingBlocked(f"예상하지 못한 클래스 구성: {classes}")
    positive = classes.index(1)
    sign = 1.0 if positive == 1 else -1.0
    return FitResult(
        coef=sign * model.coef_[0].astype(np.float64),
        intercept=float(sign * model.intercept_[0]),
        info={
            "C": config.C, "class_weight": config.class_weight, "max_iter": config.max_iter,
            "random_seed": config.random_seed, "n_iter": int(np.max(model.n_iter_)), "converged": converged,
            "sklearn_version": sklearn.__version__, "n_samples": int(len(labels)),
            "n_positive": int(labels.sum()), "n_negative": int(len(labels) - labels.sum()),
        },
    )


def group_folds(labels: np.ndarray, groups: list[str], folds: int, seed: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """같은 그룹(문서 계열)이 train과 검증에 동시에 들어가지 않는 층화 그룹 분할."""
    from sklearn.model_selection import StratifiedGroupKFold

    labels = np.asarray(labels).astype(int)
    unique_groups = sorted(set(groups))
    positive_groups = {group for group, label in zip(groups, labels) if label == 1}
    negative_groups = {group for group, label in zip(groups, labels) if label == 0}
    usable = min(folds, len(unique_groups), len(positive_groups), len(negative_groups))
    if usable < 2:
        raise TrainingBlocked(
            "그룹 교차검증을 하려면 YES가 있는 문서 계열과 NO가 있는 문서 계열이 각각 2개 이상 필요합니다."
        )
    splitter = StratifiedGroupKFold(n_splits=usable, shuffle=True, random_state=seed)
    return [(train, test) for train, test in splitter.split(np.zeros(len(labels)), labels, groups)]


def out_of_fold_scores(features: np.ndarray, labels: np.ndarray, groups: list[str], config: ClassifierConfig,
                       folds: int, sample_weight: np.ndarray | None = None,
                       always_train: np.ndarray | None = None) -> tuple[np.ndarray, int]:
    """그룹 교차검증의 out-of-fold 후보 점수.

    always_train: 검증에는 쓰지 않고 항상 학습에만 넣는 행(예: 합성 seed 예문)의 불리언 마스크.
    반환: (점수 배열 — always_train 행은 NaN, 실제 사용한 fold 수)
    """
    from .predict import LinearModel

    labels = np.asarray(labels).astype(int)
    count = len(labels)
    always = np.zeros(count, dtype=bool) if always_train is None else np.asarray(always_train, dtype=bool)
    eval_index = np.flatnonzero(~always)
    eval_groups = [groups[i] for i in eval_index]
    scores = np.full(count, np.nan)
    splits = group_folds(labels[eval_index], eval_groups, folds, config.random_seed)
    for train_local, test_local in splits:
        train = np.concatenate([eval_index[train_local], np.flatnonzero(always)])
        test = eval_index[test_local]
        if len(set(labels[train].tolist())) < 2:
            continue
        weight = None if sample_weight is None else sample_weight[train]
        fit = fit_logistic(features[train], labels[train], config, weight)
        scores[test] = LinearModel(fit.coef, fit.intercept).scores(features[test])
    return scores, len(splits)
