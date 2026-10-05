"""시험 공통 도구: 가짜 tokenizer·인코더, 합성 말뭉치, 라벨링 도우미.

가짜 구성 요소는 시험에서만 주입한다. 운영 경로(build_services 기본값)는 항상 로컬 E5를 쓴다.
"""
from __future__ import annotations

import hashlib
import random
import re
from pathlib import Path
from typing import Any

import numpy as np

from patent_marker.config import AppConfig, validate
from patent_marker.feedback.events import record_feedback
from patent_marker.services import Services, build_services

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "synthetic"
MODEL_DIR = REPO_ROOT / "models" / "multilingual-e5-small"


class FakeTokenizer:
    """공백이 아닌 문자 최대 4개를 한 토큰으로 본다."""

    overhead = 3
    identity = "fake-tokenizer-v1"

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in re.finditer(r"\S{1,4}", text)]

    def count(self, text: str) -> int:
        return len(self.offsets(text))


class FakeEncoder:
    """문자 n-gram 해시 임베딩. 결정적이며 모델 파일이 필요 없다."""

    dimension = 64
    encoder_revision = "fake-revision"
    tokenizer_hash = "fake-tokenizer-hash"
    config_hash = hashlib.sha256(b"fake-encoder-64").hexdigest()

    def __init__(self) -> None:
        self.calls = 0
        self.encoded = 0

    def encode(self, inputs: list[str]) -> np.ndarray:
        out = np.zeros((len(inputs), self.dimension), dtype=np.float32)
        for row, text in enumerate(inputs):
            for size in (2, 3):
                for start in range(len(text) - size + 1):
                    digest = hashlib.blake2b(text[start:start + size].encode("utf-8"), digest_size=4).digest()
                    value = int.from_bytes(digest, "little")
                    out[row, value % self.dimension] += 1.0 if (value >> 8) & 1 else -1.0
            norm = float(np.linalg.norm(out[row]))
            if norm:
                out[row] /= norm
        self.calls += 1
        self.encoded += len(inputs)
        return out


def make_config(base: Path, **overrides: Any) -> AppConfig:
    """임시 디렉터리를 쓰는 설정. overrides는 'section.key' 형식."""
    config = AppConfig(config_dir=base)
    config.paths.base_dir = "."
    config.runtime.offline = False
    config.review.reviewer_id = "reviewer-01"
    for key, value in overrides.items():
        section, name = key.split(".")
        setattr(getattr(config, section), name, value)
    return validate(config)


def make_services(base: Path, **overrides: Any) -> Services:
    return build_services(make_config(base, **overrides), tokenizer=FakeTokenizer(), encoder=FakeEncoder())


# ---------------------------------------------------------------- 합성 말뭉치
_SENSORS = ["레이더", "카메라", "라이다", "온도 센서", "압력 센서", "전류 센서", "가속도계", "유량계"]
_ACTUATORS = ["밸브", "펌프", "팬", "히터", "모터", "릴레이", "댐퍼", "인버터"]
_TEAMS = ["설계팀", "평가팀", "기획팀", "품질팀", "구매팀", "생산팀"]
_YES_TEMPLATES = [
    "{s} 측정값이 임계값 {n}을 넘으면 {a} 출력을 {m}% 낮추고 기준 이하에서 이전 값으로 복귀한다.",
    "{s} 신호의 이동 평균으로 임계값을 갱신하고 편차에 비례해 {a} 개도를 보정한다.",
    "{s} 신뢰도가 {n} 미만이면 가중치를 {m}% 줄이고 {a} 제어 입력을 보상 필터로 전환한다.",
    "{a} 구동 주파수를 {s} 구간별로 다르게 설정하고 전환 시 히스테리시스 폭 {n}을 적용한다.",
    "{s} 오차를 {n}개 샘플로 추정하여 {a} 지령값에서 차감하는 보정 알고리즘을 적용한다.",
]
_NO_TEMPLATES = [
    "{month}월 {week}주차에 {t} 주간 회의를 진행한다.",
    "예산 집행률은 {m}%이며 잔여 예산은 다음 분기로 이월한다.",
    "다음 분기에 {t} 일정을 재조정할 예정이다.",
    "{t} 인력 {n}명 충원을 요청하였다.",
    "{month}월 보고서는 {t}에서 작성하여 공유 폴더에 올린다.",
    "성능 개선을 지속적으로 추진할 계획이다.",
    "시험 결과 모든 항목이 목표를 만족하였다.",
]


def write_corpus(directory: Path, families: int = 24, yes_per_doc: int = 5, no_per_doc: int = 7,
                 seed: int = 7) -> dict[str, str]:
    """문서 계열마다 TXT 하나를 만든다. 반환: {문단 텍스트: 'YES'|'NO'}."""
    directory.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    expected: dict[str, str] = {}
    for family in range(families):
        paragraphs: list[tuple[str, str]] = []
        while sum(label == "YES" for _, label in paragraphs) < yes_per_doc:
            text = rng.choice(_YES_TEMPLATES).format(s=rng.choice(_SENSORS), a=rng.choice(_ACTUATORS),
                                                     n=rng.randint(2, 90), m=rng.randint(5, 60))
            if text not in expected:
                expected[text] = "YES"
                paragraphs.append((text, "YES"))
        while sum(label == "NO" for _, label in paragraphs) < no_per_doc:
            text = rng.choice(_NO_TEMPLATES).format(t=rng.choice(_TEAMS), month=rng.randint(1, 12),
                                                    week=rng.randint(1, 4), n=rng.randint(1, 9), m=rng.randint(30, 99))
            text = f"{text} (과제 {family}-{len(paragraphs)})"
            if text not in expected:
                expected[text] = "NO"
                paragraphs.append((text, "NO"))
        rng.shuffle(paragraphs)
        body = "\n\n".join(text for text, _ in paragraphs) + "\n"
        (directory / f"report_{family:02d}.txt").write_text(body, encoding="utf-8")
    return expected


def label_everything(services: Services, expected: dict[str, str], reviewer: str = "reviewer-01") -> int:
    """기대 라벨이 있는 모든 segment에 사람 라벨을 기록한다."""
    rows = services.database.query(
        "SELECT s.segment_id, s.normalized_text FROM segments s JOIN documents d ON d.document_id = s.document_id "
        "WHERE d.is_current = 1 ORDER BY s.document_id, s.seq")
    count = 0
    for row in rows:
        label = expected.get(row["normalized_text"])
        if label:
            record_feedback(services.database, target_type="segment", target_id=row["segment_id"], label=label,
                            reviewer_id=reviewer, guideline_version="1.0")
            count += 1
    return count
