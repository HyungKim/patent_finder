"""후보 threshold 선택과 정책 버전 (스펙 7.3).

threshold는 검증 집합에서 Recall 목표를 만족하는 값 중 검토량이 가장 적은(가장 높은) 값이다.
기본값 0.5 같은 고정값을 쓰지 않으며, threshold 변경은 항상 새 정책 버전으로 남긴다.
"""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np

from ..runtime import utc_now
from ..storage import Database

CANDIDATE = "CANDIDATE"
NOT_CANDIDATE = "NOT_CANDIDATE"
# 같은 점수를 다른 배치로 다시 계산할 때 생기는 마지막 자리 차이를 흡수한다.
_EPSILON = 1e-9

# 정책 상태
VALIDATED = "VALIDATED"  # 검증 partition에서 선택
CV_ESTIMATE = "CV_ESTIMATE"  # 검증 표본이 부족해 그룹 교차검증의 out-of-fold 점수로 추정
SEED_UNVALIDATED = "SEED_UNVALIDATED"  # 합성 seed 예문으로만 정함. 실제 문서에서 검증되지 않음
UNVALIDATED = "UNVALIDATED"  # threshold 없음


def select_threshold(scores: np.ndarray, labels: np.ndarray, recall_target: float) -> dict[str, Any] | None:
    """Recall >= recall_target을 만족하는 가장 높은 threshold. 양성이 없으면 None."""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels).astype(int)
    positives = np.sort(scores[labels == 1])[::-1]
    if len(positives) == 0:
        return None
    needed = min(len(positives), max(1, int(math.ceil(recall_target * len(positives) - 1e-9))))
    threshold = float(positives[needed - 1]) - _EPSILON
    flagged = scores >= threshold
    true_positive = int((flagged & (labels == 1)).sum())
    return {
        "threshold": threshold,
        "recall_target": recall_target,
        "recall": true_positive / len(positives),
        "precision": true_positive / int(flagged.sum()) if flagged.any() else None,
        "review_ratio": float(flagged.mean()),
        "n": int(len(labels)),
        "n_positive": int(len(positives)),
    }


def decide(score: float | None, threshold: float | None) -> str | None:
    """score >= threshold이면 후보. 점수나 threshold가 없으면 판정하지 않는다(None)."""
    if score is None or threshold is None:
        return None
    return CANDIDATE if score >= threshold else NOT_CANDIDATE


def create_policy(database: Database, model_version: str, threshold: float | None, status: str,
                  selection: dict[str, Any]) -> str:
    with database.transaction() as connection:
        count = connection.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
        policy_version = f"policy-{count + 1:04d}"
        connection.execute(
            "INSERT INTO policies (policy_version, model_version, threshold, status, selection_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (policy_version, model_version, threshold, status, json.dumps(selection, ensure_ascii=False), utc_now()),
        )
    return policy_version


def get_policy(database: Database, policy_version: str | None) -> dict[str, Any] | None:
    if not policy_version:
        return None
    row = database.query_one("SELECT * FROM policies WHERE policy_version = ?", (policy_version,))
    if row is None:
        return None
    data = dict(row)
    data["selection"] = json.loads(data.pop("selection_json"))
    return data
