from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from helpers import LAYA_MODEL_DIR, MODEL_DIR, FakeEncoder, FakeTokenizer, make_config, make_services, write_corpus
from patent_marker import runtime
from patent_marker.services import build_services


def _laya_environment() -> bool:
    """Laya SDK와 PyTorch가 설치된 별도 환경이고, 받아 둔 모델이 있는가."""
    return (all(importlib.util.find_spec(name) is not None for name in ("laya", "torch"))
            and (LAYA_MODEL_DIR / "manifest.json").is_file())


def pytest_collection_modifyitems(config, items):
    skips = {}
    if not (MODEL_DIR / "manifest.json").is_file():
        skips["requires_model"] = pytest.mark.skip(
            reason=f"로컬 E5 모델이 없습니다: {MODEL_DIR} (docs/OFFLINE_INSTALL.md 참고)")
    if not _laya_environment():
        skips["requires_laya"] = pytest.mark.skip(
            reason="Laya 실험 환경(.venv-laya)과 받아 둔 Laya 모델이 있어야 합니다 (docs/LAYA_EXPERIMENT.md 참고)")
    for item in items:
        for keyword, skip in skips.items():
            if keyword in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _restore_network_guard():
    """시험이 설치한 네트워크 가드가 다른 시험에 남지 않게 한다."""
    yield
    runtime.uninstall_network_guard()


@pytest.fixture
def tokenizer() -> FakeTokenizer:
    return FakeTokenizer()


@pytest.fixture
def config(tmp_path: Path):
    return make_config(tmp_path)


@pytest.fixture
def services(tmp_path: Path):
    return make_services(tmp_path)


@pytest.fixture
def corpus(tmp_path: Path):
    directory = tmp_path / "inbox"
    return directory, write_corpus(directory)


@pytest.fixture
def real_services(tmp_path: Path):
    """실제 E5(ONNX)와 오프라인 가드를 쓰는 서비스."""
    config = make_config(tmp_path, **{"paths.e5": str(MODEL_DIR), "runtime.offline": True})
    return build_services(config)
