"""평가 지표 (스펙 12.2). 분모가 0이면 0이나 1로 꾸미지 않고 None(N/A)으로 둔다."""
from __future__ import annotations

from typing import Any

import numpy as np


def safe_div(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def f2_score(precision: float | None, recall: float | None) -> float | None:
    """F2 = 5PR / (4P + R). Recall에 더 큰 가중."""
    if precision is None or recall is None or (4 * precision + recall) == 0:
        return None
    return 5 * precision * recall / (4 * precision + recall)


def binary_metrics(truth: np.ndarray, flagged: np.ndarray) -> dict[str, Any]:
    truth = np.asarray(truth).astype(bool)
    flagged = np.asarray(flagged).astype(bool)
    tp = int((truth & flagged).sum())
    fp = int((~truth & flagged).sum())
    fn = int((truth & ~flagged).sum())
    tn = int((~truth & ~flagged).sum())
    recall = safe_div(tp, tp + fn)
    precision = safe_div(tp, tp + fp)
    return {
        "n": int(len(truth)), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "recall": recall, "precision": precision, "f2": f2_score(precision, recall),
        "review_ratio": safe_div(tp + fp, len(truth)),
    }


def average_precision(truth: np.ndarray, scores: np.ndarray) -> float | None:
    """PR 곡선의 계단식 합(scikit-learn average_precision_score, 보간 없음)."""
    truth = np.asarray(truth).astype(int)
    if truth.sum() == 0 or len(truth) == 0:
        return None
    from sklearn.metrics import average_precision_score

    return float(average_precision_score(truth, np.asarray(scores, dtype=float)))


def group_bootstrap_ci(truth: np.ndarray, flagged: np.ndarray, groups: list[str], samples: int, seed: int,
                       metric: str = "recall") -> dict[str, Any] | None:
    """문서 계열 단위 bootstrap으로 구한 95% 신뢰구간. 그룹이 2개 미만이면 N/A."""
    truth = np.asarray(truth).astype(bool)
    flagged = np.asarray(flagged).astype(bool)
    unique = sorted(set(groups))
    if len(unique) < 2 or samples <= 0:
        return None
    index_of = {group: np.flatnonzero(np.array(groups) == group) for group in unique}
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(samples):
        chosen = rng.integers(0, len(unique), size=len(unique))
        rows = np.concatenate([index_of[unique[i]] for i in chosen])
        value = binary_metrics(truth[rows], flagged[rows])[metric]
        if value is not None:
            values.append(value)
    if len(values) < max(10, samples // 10):
        return None
    low, high = np.percentile(values, [2.5, 97.5])
    return {"low": float(low), "high": float(high), "method": "document-family bootstrap, percentile",
            "samples": len(values), "groups": len(unique)}


def calibration(truth: np.ndarray, scores: np.ndarray, bins: int = 10) -> dict[str, Any] | None:
    """점수를 확률로 해석할 때의 보정 품질: Brier score, ECE, reliability 표."""
    truth = np.asarray(truth).astype(float)
    scores = np.asarray(scores, dtype=float)
    if len(truth) == 0:
        return None
    edges = np.linspace(0.0, 1.0, bins + 1)
    table = []
    ece = 0.0
    for low, high in zip(edges, edges[1:]):
        mask = (scores >= low) & ((scores < high) if high < 1.0 else (scores <= high))
        count = int(mask.sum())
        if count == 0:
            continue
        mean_score = float(scores[mask].mean())
        positive_rate = float(truth[mask].mean())
        ece += abs(mean_score - positive_rate) * count / len(truth)
        table.append({"low": float(low), "high": float(high), "n": count, "mean_score": mean_score,
                      "positive_rate": positive_rate})
    return {"brier": float(np.mean((scores - truth) ** 2)), "ece": float(ece), "bins": table}


def document_candidate_recall(truth: np.ndarray, flagged: np.ndarray, documents: list[str]) -> dict[str, Any]:
    """양성이 있는 문서 중 하나 이상 회수한 문서 비율. 구간 Recall의 대체 지표가 아니다."""
    truth = np.asarray(truth).astype(bool)
    flagged = np.asarray(flagged).astype(bool)
    positive_docs: set[str] = set()
    retrieved_docs: set[str] = set()
    for is_positive, is_flagged, document in zip(truth, flagged, documents):
        if is_positive:
            positive_docs.add(document)
            if is_flagged:
                retrieved_docs.add(document)
    return {"documents_with_positive": len(positive_docs), "documents_retrieved": len(retrieved_docs),
            "ratio": safe_div(len(retrieved_docs), len(positive_docs))}


def language_mix(text: str) -> str:
    hangul = sum(1 for ch in text if "가" <= ch <= "힣")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = hangul + latin
    if total == 0:
        return "other"
    if hangul == 0:
        return "english"
    # 한글 음절 하나가 영문 여러 글자에 해당하므로 영문자 비중이 작을 때만 한국어 중심으로 본다.
    return "korean" if latin / total <= 0.2 else "mixed"


def length_bucket(tokens: int) -> str:
    return "short(<=32)" if tokens <= 32 else "medium(33-128)" if tokens <= 128 else "long(>128)"


def sliced_metrics(truth: np.ndarray, flagged: np.ndarray, keys: list[str]) -> dict[str, Any]:
    truth = np.asarray(truth)
    flagged = np.asarray(flagged)
    result = {}
    for key in sorted(set(keys)):
        mask = np.array([item == key for item in keys])
        result[key] = binary_metrics(truth[mask], flagged[mask])
    return result
