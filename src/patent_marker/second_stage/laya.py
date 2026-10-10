"""Laya 2단계 판단 (실험용, 스펙 8절).

- 기본 설치에는 없다. Laya SDK와 PyTorch가 있는 별도 환경(.venv-laya)의 Python에서만 불러온다.
- 모델은 받아 둔 로컬 폴더에서만 읽는다. manifest에 적힌 해시를 모두 확인한 뒤 작업용 사본을 만들어
  거기서 불러온다. SDK가 불러올 때 tokenizer 설정 파일을 고쳐 쓰는데, 그 변경이 확인된 원본 폴더에
  닿지 않게 하기 위해서다.
- 질문과 '동의' 기준값은 사람 라벨로 검증되지 않았다. 합성 자료에서 잰 결과는 docs/LAYA_EXPERIMENT.md에 있다.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Sequence

from ..runtime import sha256_file
from .adapter import Assessment

QUESTION_VERSION = "laya-q1"
QUESTION_ID = "means"
QUESTION = {QUESTION_ID: {
    "type": "noul",
    "instructions": "대상 문장에 구체적인 구성, 처리 순서, 제어 방법 또는 수치 조건이 적혀 있는가?",
}}
DEFAULT_AGREE_AT = 0.5  # 예/아니오 질문의 자연스러운 경계. 검증된 값이 아니다
DEFAULT_MODEL_DIR = "models/laya-multilingual"
# SDK에 넘겨 한 번 더 확인하게 하는 파일. tokenizer 설정은 SDK가 고쳐 쓰므로 여기서 뺀다(원본은 아래에서 직접 확인한다).
_SDK_CHECKED = ("model.safetensors", "tokenizer/tokenizer.json", "rl_agent_config.json", "encoder/config.json")
_RUNTIME_FILES = ("rl_agent_config.json", "encoder/config.json", "tokenizer/tokenizer.json",
                  "tokenizer/tokenizer_config.json", "model.safetensors")
_WEIGHTS = "model.safetensors"


class LayaUnavailable(RuntimeError):
    pass


def read_manifest(model_dir: Path) -> dict[str, Any]:
    path = Path(model_dir) / "manifest.json"
    if not path.is_file():
        raise LayaUnavailable(
            f"Laya 모델이 없습니다: {model_dir}. 자동으로 내려받지 않습니다. "
            "준비 환경에서 tools/fetch_laya_model.py로 받아 두세요."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def verify_model_dir(model_dir: Path, manifest: dict[str, Any]) -> None:
    """manifest에 적힌 파일이 모두 있고 해시가 같은지 확인한다."""
    for name, meta in manifest["files"].items():
        path = Path(model_dir) / name
        if not path.is_file():
            raise LayaUnavailable(f"Laya 모델 파일이 없습니다: {path}. 자동으로 내려받지 않습니다.")
        if sha256_file(path) != meta["sha256"]:
            raise LayaUnavailable(f"Laya 모델 파일의 해시가 manifest와 다릅니다: {path}")


def prepare_runtime(model_dir: Path, runtime_root: Path, manifest: dict[str, Any]) -> Path:
    """SDK가 읽을 작업용 사본을 만든다. 원본 폴더는 건드리지 않는다.

    작은 설정 파일은 매번 원본에서 다시 복사해서 SDK가 고쳐 쓴 내용이 다음 실행에 남지 않게 한다.
    가중치 파일은 크므로 하드 링크로 두고, 링크를 만들 수 없는 파일 시스템이면 복사한다.
    """
    model_dir, runtime = Path(model_dir), Path(runtime_root) / manifest["revision"][:12]
    for name in _RUNTIME_FILES:
        source, target = model_dir / name, runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == _WEIGHTS:
            if target.exists() and target.stat().st_size == source.stat().st_size:
                continue  # 내용은 SDK가 불러오기 전에 해시로 다시 확인한다
            target.unlink(missing_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        else:
            shutil.copyfile(source, target)
    return runtime


def compose_state(target: str, context: str) -> str:
    """Laya에 보여 줄 글. 문맥(슬라이드 제목 등)이 있으면 대상 문장과 구분해 함께 준다."""
    context = (context or "").strip()
    if not context or context == target.strip():
        return target
    return f"슬라이드 제목: {context}\n대상 문장: {target}"


class LayaSecondStage:
    """받아 둔 Laya-multilingual을 CPU에서 불러 1차 후보를 다시 판단한다."""

    def __init__(self, model_dir: Path, runtime_root: Path, threads: int = 4,
                 agree_at: float = DEFAULT_AGREE_AT, batch_size: int = 16) -> None:
        manifest = read_manifest(model_dir)
        verify_model_dir(model_dir, manifest)
        try:
            import laya
            import torch
        except ImportError as exc:
            raise LayaUnavailable(
                "Laya 실험 환경이 아닙니다. Laya와 PyTorch가 설치된 별도 환경(.venv-laya)의 Python으로 실행하세요. "
                "(docs/LAYA_EXPERIMENT.md)"
            ) from exc
        runtime = prepare_runtime(model_dir, runtime_root, manifest)
        torch.set_num_threads(threads)
        started = time.perf_counter()
        self._agent = laya.load(str(runtime), device="cpu",
                                expected_sha256={name: manifest["files"][name]["sha256"] for name in _SDK_CHECKED})
        self.load_seconds = time.perf_counter() - started
        self.model_version = f"{manifest['model_id']}@{manifest['revision'][:12]}"
        self.question_version = QUESTION_VERSION
        self.agree_at = agree_at
        self.batch_size = batch_size

    def assess_batch(self, items: Sequence[tuple[str, str]]) -> list[Assessment]:
        """(대상 문장, 문맥) 목록을 판단한다. 한 묶음이 실패해도 나머지는 계속한다."""
        assessments: list[Assessment] = []
        for start in range(0, len(items), self.batch_size):
            chunk = items[start: start + self.batch_size]
            states = [compose_state(target, context) for target, context in chunk]
            started = time.perf_counter()
            try:
                results = self._agent.predict_batch(states, QUESTION, batch_size=len(states))
                scored = [(float(result["answers"][QUESTION_ID]["noul"]),
                           bool(result.get("usage", {}).get("truncated"))) for result in results]
                if len(scored) != len(chunk) or not all(0.0 <= score <= 1.0 for score, _ in scored):
                    raise ValueError("Laya 결과의 수 또는 점수 범위가 예상과 다르다")
            except Exception as exc:  # 2단계의 오류가 1차 결과를 막지 않게 한다
                assessments.extend(
                    Assessment(status="error", model_version=self.model_version,
                               question_version=self.question_version, error_code=type(exc).__name__)
                    for _ in chunk
                )
                continue
            latency = (time.perf_counter() - started) * 1000 / len(chunk)
            for score, truncated in scored:
                assessments.append(Assessment(
                    status="ok",
                    # 원점수는 예/아니오 질문에서 '예'일 확률이다. 보정하지 않은 값이다
                    raw_scores={QUESTION_ID: score, "scale": "p_yes", "truncated": truncated},
                    decision="AGREE" if score >= self.agree_at else "DISAGREE",
                    model_version=self.model_version, question_version=self.question_version,
                    latency_ms=round(latency, 2),
                ))
        return assessments

    def assess(self, target: str, context: str) -> Assessment:
        return self.assess_batch([(target, context)])[0]
