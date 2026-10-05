"""분류기 bundle: 가중치 + 인코더·전처리·특징 설정 + 학습 이력을 하나의 JSON으로 묶는다 (스펙 10절).

pickle을 쓰지 않으므로 파일을 열어 내용을 확인할 수 있고, scikit-learn 버전과 무관하게 추론한다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..runtime import atomic_write_text, sha256_file
from ..versions import BUNDLE_SCHEMA_VERSION, PREPROCESS_VERSION, SEGMENTATION_VERSION
from .predict import LinearModel


class BundleError(RuntimeError):
    pass


@dataclass
class ModelBundle:
    model_version: str
    kind: str  # seed | trained
    coef: list[float]
    intercept: float
    feature_config: dict[str, Any]
    feature_config_hash: str
    encoder_revision: str
    encoder_config_hash: str
    tokenizer_hash: str
    preprocess_version: str = PREPROCESS_VERSION
    segmentation_version: str = SEGMENTATION_VERSION
    training: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    schema_version: int = BUNDLE_SCHEMA_VERSION

    def model(self) -> LinearModel:
        return LinearModel(np.asarray(self.coef, dtype=np.float64), float(self.intercept))

    @property
    def mode(self) -> str:
        return self.feature_config["mode"]


def save_bundle(bundle: ModelBundle, path: Path) -> str:
    """bundle을 저장하고 파일의 sha256을 돌려준다."""
    atomic_write_text(path, json.dumps(asdict(bundle), ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return sha256_file(path)


def load_bundle(path: Path, expected_sha256: str | None = None) -> ModelBundle:
    path = Path(path)
    if not path.is_file():
        raise BundleError(f"분류기 파일이 없습니다: {path}")
    if expected_sha256 is not None and sha256_file(path) != expected_sha256:
        raise BundleError(f"분류기 파일 해시가 registry와 다릅니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != BUNDLE_SCHEMA_VERSION:
            raise BundleError(f"지원하지 않는 bundle schema: {data.get('schema_version')}")
        return ModelBundle(**data)
    except (ValueError, TypeError) as exc:
        raise BundleError(f"분류기 파일 형식 오류: {path} ({exc})") from exc


def compatibility_problems(bundle: ModelBundle, encoder_config_hash: str) -> list[str]:
    """현재 런타임에서 이 bundle을 쓸 수 없는 이유 목록. 비어 있으면 호환."""
    problems = []
    if bundle.encoder_config_hash != encoder_config_hash:
        problems.append("인코더(모델 파일·tokenizer·접두사·정규화)가 학습 때와 다릅니다.")
    if bundle.preprocess_version != PREPROCESS_VERSION:
        problems.append(f"전처리 버전 불일치: bundle {bundle.preprocess_version}, 현재 {PREPROCESS_VERSION}")
    if bundle.segmentation_version != SEGMENTATION_VERSION:
        problems.append(f"분할 버전 불일치: bundle {bundle.segmentation_version}, 현재 {SEGMENTATION_VERSION}")
    if bundle.feature_config.get("encoder_config_hash") != bundle.encoder_config_hash:
        problems.append("bundle 내부의 특징 설정과 인코더 정보가 서로 다릅니다.")
    return problems
