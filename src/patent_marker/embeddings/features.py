"""분류기 입력 특징 구성 (변경안 A1).

- separate (기본): [대상 임베딩 ; 문맥 임베딩]. 문맥 임베딩은 같은 섹션의 제목·이전·다음 segment
  임베딩의 평균을 L2 정규화한 것이다. 문맥이 없으면 0 벡터.
- target_only: 대상 임베딩만.
- composed: 스펙 5.2 원안. 제목·문맥·대상을 한 문자열로 묶어 한 번 임베딩한다.

세 방식 모두 캐시를 공유하므로 `experiment` 명령으로 그룹 교차검증 비교를 할 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import FEATURE_MODES, SegmentationConfig
from ..runtime import canonical_json, sha256_text
from ..segmentation.context import compose_input
from ..segmentation.tokens import TokenCounter
from ..versions import FEATURE_VERSION
from .cache import EmbeddingStore


@dataclass
class FeatureInput:
    target: str
    title: str | None = None
    prev: str | None = None
    next: str | None = None


def feature_config(mode: str, encoder_config_hash: str, segmentation: SegmentationConfig) -> dict[str, Any]:
    if mode not in FEATURE_MODES:
        raise ValueError(f"알 수 없는 features.mode: {mode}")
    config: dict[str, Any] = {
        "mode": mode, "feature_version": FEATURE_VERSION, "encoder_config_hash": encoder_config_hash,
    }
    if mode == "composed":
        config["composed"] = {
            "max_input_tokens": segmentation.max_input_tokens,
            "context_tokens": segmentation.context_tokens,
            "title_tokens": segmentation.title_tokens,
        }
    return config


def feature_config_hash(config: dict[str, Any]) -> str:
    return sha256_text(canonical_json(config))


def feature_dimension(mode: str, embedding_dimension: int) -> int:
    return embedding_dimension * 2 if mode == "separate" else embedding_dimension


def build_features(items: list[FeatureInput], store: EmbeddingStore, mode: str,
                   counter: TokenCounter | None = None,
                   segmentation: SegmentationConfig | None = None) -> tuple[np.ndarray, list[str]]:
    """특징 행렬과, 각 행의 주 임베딩(대상 또는 결합 입력)의 embedding_id."""
    dimension = store.encoder.dimension
    if not items:
        return np.zeros((0, feature_dimension(mode, dimension)), dtype=np.float32), []
    if mode == "composed":
        if counter is None or segmentation is None:
            raise ValueError("composed 모드에는 tokenizer와 분할 설정이 필요합니다.")
        texts = [compose_input(item.target, item.title, item.prev, item.next, counter, segmentation) for item in items]
        return store.get(texts)
    if mode == "target_only":
        return store.get([item.target for item in items])

    texts: list[str] = []
    index: dict[str, int] = {}

    def position(text: str | None) -> int | None:
        if not text:
            return None
        if text not in index:
            index[text] = len(texts)
            texts.append(text)
        return index[text]

    rows = [(position(item.target), [position(item.title), position(item.prev), position(item.next)]) for item in items]
    vectors, ids = store.get(texts)
    features = np.zeros((len(items), dimension * 2), dtype=np.float32)
    primary: list[str] = []
    for row, (target, context) in enumerate(rows):
        features[row, :dimension] = vectors[target]
        used = [c for c in context if c is not None and c != target]
        if used:
            mean = vectors[used].mean(axis=0)
            norm = float(np.linalg.norm(mean))
            if norm > 1e-12:
                features[row, dimension:] = mean / norm
        primary.append(ids[target])
    return features, primary
