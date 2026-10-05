"""선형 분류기 추론. 점수는 '후보 점수'이며 등록 확률이 아니다 (스펙 7.1)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class LinearModel:
    coef: np.ndarray  # [D], float64
    intercept: float

    def decision(self, features: np.ndarray) -> np.ndarray:
        if features.shape[1] != self.coef.shape[0]:
            raise ValueError(f"특징 차원({features.shape[1]})이 분류기({self.coef.shape[0]})와 다릅니다.")
        return features.astype(np.float64) @ self.coef + self.intercept

    def scores(self, features: np.ndarray) -> np.ndarray:
        """0~1 범위의 후보 점수."""
        z = self.decision(features)
        return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))
