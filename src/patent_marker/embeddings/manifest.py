"""로컬 모델 디렉터리 검증 (스펙 2절, 6절).

모델은 검증된 로컬 디렉터리에서만 로드한다. 파일이 없거나 해시가 다르면
다운로드를 시도하지 않고 명시적 오류를 낸다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..runtime import sha256_file

REQUIRED_FILES = ("model.onnx", "tokenizer.json", "config.json")


class ModelError(RuntimeError):
    """로컬 모델이 없거나 검증에 실패함."""


@dataclass(frozen=True)
class ModelInfo:
    model_dir: Path
    model_id: str
    revision: str
    license: str
    files: dict[str, str]  # 파일명 -> sha256
    dimension: int
    max_tokens: int

    @property
    def encoder_hash(self) -> str:
        return self.files["model.onnx"]

    @property
    def tokenizer_hash(self) -> str:
        return self.files["tokenizer.json"]


def load_model_info(model_dir: Path, verify_hashes: bool = True) -> ModelInfo:
    model_dir = Path(model_dir)
    hint = "모델은 자동으로 내려받지 않습니다. docs/OFFLINE_INSTALL.md의 반입 절차로 준비하세요."
    if not model_dir.is_dir():
        raise ModelError(f"로컬 모델 디렉터리가 없습니다: {model_dir}. {hint}")
    manifest_path = model_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ModelError(f"모델 manifest가 없습니다: {manifest_path}. {hint}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = {name: meta["sha256"] for name, meta in manifest["files"].items()}
        embedding = manifest["embedding"]
        info = ModelInfo(
            model_dir=model_dir, model_id=manifest["model_id"], revision=manifest["revision"],
            license=manifest.get("license", "unknown"), files=files,
            dimension=int(embedding["dimension"]), max_tokens=int(embedding["max_tokens"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ModelError(f"모델 manifest 형식 오류: {manifest_path} ({exc})") from exc
    for name in REQUIRED_FILES:
        if name not in files:
            raise ModelError(f"manifest에 필수 파일 {name}의 해시가 없습니다.")
        if not (model_dir / name).is_file():
            raise ModelError(f"모델 파일이 없습니다: {model_dir / name}. {hint}")
    if verify_hashes:
        for name, expected in files.items():
            path = model_dir / name
            if not path.is_file():
                raise ModelError(f"manifest에 기록된 파일이 없습니다: {path}")
            actual = sha256_file(path)
            if actual != expected:
                raise ModelError(f"모델 파일 해시 불일치: {name} (기대 {expected[:12]}…, 실제 {actual[:12]}…)")
    return info
