"""지표, threshold, 학습, bundle, 캐시, 설정 제약, 오프라인 가드 시험 (스펙 2·6·7·12절, 17.1)."""
from __future__ import annotations

import socket
from pathlib import Path

import numpy as np
import pytest

from helpers import FakeEncoder, FakeTokenizer, make_config
from patent_marker import runtime
from patent_marker.classifiers.bundle import BundleError, ModelBundle, compatibility_problems, load_bundle, save_bundle
from patent_marker.classifiers.predict import LinearModel
from patent_marker.classifiers.train import TrainingBlocked, fit_logistic, group_folds, out_of_fold_scores
from patent_marker.config import AppConfig, ClassifierConfig, ConfigError, SegmentationConfig, load_config, validate
from patent_marker.embeddings.cache import EmbeddingStore
from patent_marker.embeddings.features import FeatureInput, build_features, feature_config, feature_config_hash
from patent_marker.embeddings.manifest import ModelError, load_model_info
from patent_marker.evaluation import metrics
from patent_marker.policies.aggregation import paragraph_score, paragraph_truth
from patent_marker.policies.thresholds import decide, select_threshold
from patent_marker.storage import open_database


# ---------------------------------------------------------------- 지표
def test_metrics_formulas_and_not_applicable_values():
    truth = np.array([1, 1, 1, 0, 0, 0, 0, 0])
    flagged = np.array([1, 1, 0, 1, 0, 0, 0, 0]).astype(bool)
    result = metrics.binary_metrics(truth, flagged)
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (2, 1, 1, 4)
    assert result["recall"] == pytest.approx(2 / 3) and result["precision"] == pytest.approx(2 / 3)
    assert result["f2"] == pytest.approx(5 * (2 / 3) * (2 / 3) / (4 * (2 / 3) + (2 / 3)))
    assert result["review_ratio"] == pytest.approx(3 / 8)
    empty = metrics.binary_metrics(np.array([0, 0]), np.array([False, False]))
    assert empty["recall"] is None and empty["precision"] is None and empty["f2"] is None  # 0이나 1로 꾸미지 않는다
    assert metrics.average_precision(np.array([0, 0]), np.array([0.1, 0.2])) is None
    assert metrics.average_precision(np.array([1, 0]), np.array([0.9, 0.2])) == pytest.approx(1.0)


def test_bootstrap_ci_needs_multiple_groups():
    truth = np.array([1, 1, 0, 0])
    flagged = np.array([True, False, False, False])
    assert metrics.group_bootstrap_ci(truth, flagged, ["a"] * 4, samples=200, seed=1) is None
    ci = metrics.group_bootstrap_ci(np.tile(truth, 5), np.tile(flagged, 5),
                                    [f"g{i}" for i in range(5) for _ in range(4)], samples=300, seed=1)
    assert ci["low"] <= 0.5 <= ci["high"] and ci["groups"] == 5


def test_calibration_and_slices():
    truth = np.array([1, 0, 1, 0])
    scores = np.array([0.9, 0.1, 0.8, 0.2])
    cal = metrics.calibration(truth, scores)
    assert cal["brier"] == pytest.approx(np.mean((scores - truth) ** 2)) and 0 <= cal["ece"] <= 1
    assert metrics.language_mix("가중치를 조절한다") == "korean"
    assert metrics.language_mix("Radar confidence가 낮으면 camera weight 증가") == "mixed"
    assert metrics.language_mix("switch to the backup sensor") == "english"
    assert metrics.length_bucket(10) == "short(<=32)" and metrics.length_bucket(200) == "long(>128)"
    doc = metrics.document_candidate_recall(truth, scores >= 0.85, ["d1", "d1", "d2", "d2"])
    assert doc == {"documents_with_positive": 2, "documents_retrieved": 1, "ratio": 0.5}


# ---------------------------------------------------------------- threshold
def test_threshold_is_highest_value_meeting_recall_target():
    scores = np.array([0.95, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
    labels = np.array([1, 1, 0, 1, 0, 1, 0, 0, 0, 0])
    selection = select_threshold(scores, labels, 0.95)
    assert selection["recall"] == 1.0 and selection["threshold"] == pytest.approx(0.5, abs=1e-6)
    assert selection["review_ratio"] == pytest.approx(0.6)
    relaxed = select_threshold(scores, labels, 0.75)
    assert relaxed["threshold"] == pytest.approx(0.7, abs=1e-6) and relaxed["recall"] == 0.75
    assert select_threshold(scores, np.zeros(10, dtype=int), 0.95) is None  # 양성이 없으면 정하지 않는다
    assert decide(0.5, selection["threshold"]) == "CANDIDATE" and decide(0.49, selection["threshold"]) == "NOT_CANDIDATE"
    assert decide(None, 0.5) is None and decide(0.9, None) is None  # 미실행을 NO로 바꾸지 않는다


def test_paragraph_aggregation_rules():
    assert paragraph_score([0.2, None, 0.7]) == 0.7 and paragraph_score([None]) is None
    assert paragraph_truth(["NO", "YES"]) == "YES"
    assert paragraph_truth(["NO", "NO"]) == "NO"
    assert paragraph_truth(["NO", None]) is None  # 일부만 검토된 문단은 미정
    assert paragraph_truth([None], paragraph_label="NO") == "NO"


# ---------------------------------------------------------------- 학습
def _toy(n=120, seed=0):
    rng = np.random.default_rng(seed)
    labels = np.array([i % 2 for i in range(n)])
    features = rng.normal(size=(n, 8)) + labels[:, None] * 1.5
    groups = [f"g{i % 12}" for i in range(n)]
    return features.astype(np.float32), labels, groups


def test_logistic_training_uses_positive_class_and_blocks_single_class():
    features, labels, _ = _toy()
    fit = fit_logistic(features, labels, ClassifierConfig())
    assert fit.info["converged"] and fit.info["n_positive"] == 60
    scores = LinearModel(fit.coef, fit.intercept).scores(features)
    assert scores[labels == 1].mean() > 0.7 > 0.3 > scores[labels == 0].mean()
    with pytest.raises(TrainingBlocked):
        fit_logistic(features, np.ones(len(labels), dtype=int), ClassifierConfig())
    with pytest.raises(ValueError):
        LinearModel(fit.coef, fit.intercept).scores(np.zeros((2, 5)))


def test_group_folds_never_split_a_group():
    features, labels, groups = _toy()
    folds = group_folds(labels, groups, 5, seed=42)
    assert len(folds) == 5
    for train, test in folds:
        assert not ({groups[i] for i in train} & {groups[i] for i in test})
    scores, used = out_of_fold_scores(features, labels, groups, ClassifierConfig(), 5)
    assert used == 5 and not np.isnan(scores).any()
    always = np.zeros(len(labels), dtype=bool)
    always[:20] = True
    scores, _ = out_of_fold_scores(features, labels, groups, ClassifierConfig(), 5, always_train=always)
    assert np.isnan(scores[:20]).all() and not np.isnan(scores[20:]).any()
    with pytest.raises(TrainingBlocked):
        group_folds(labels, ["one"] * len(labels), 5, seed=1)


# ---------------------------------------------------------------- bundle
def test_bundle_roundtrip_hash_and_compatibility(tmp_path):
    cfg = feature_config("separate", "enc-hash", SegmentationConfig())
    bundle = ModelBundle(model_version="classifier-0001", kind="trained", coef=[0.5, -0.25], intercept=0.1,
                         feature_config=cfg, feature_config_hash=feature_config_hash(cfg), encoder_revision="rev",
                         encoder_config_hash="enc-hash", tokenizer_hash="tok")
    path = tmp_path / "models" / "classifier-0001.json"
    digest = save_bundle(bundle, path)
    loaded = load_bundle(path, expected_sha256=digest)
    assert loaded.coef == [0.5, -0.25] and loaded.mode == "separate"
    assert compatibility_problems(loaded, "enc-hash") == []
    assert compatibility_problems(loaded, "other-encoder")  # 인코더가 다르면 쓸 수 없다
    loaded.segmentation_version = "segmentation-0"
    assert any("분할 버전" in problem for problem in compatibility_problems(loaded, "enc-hash"))
    path.write_text(path.read_text(encoding="utf-8").replace("0.5", "0.6"), encoding="utf-8")
    with pytest.raises(BundleError):
        load_bundle(path, expected_sha256=digest)  # 변조된 파일은 로드하지 않는다


# ---------------------------------------------------------------- 임베딩 캐시·특징
def test_embedding_cache_reuses_vectors_and_recovers_from_corrupt_shard(tmp_path):
    database = open_database(tmp_path / "db.sqlite3")
    encoder = FakeEncoder()
    store = EmbeddingStore(database, tmp_path, encoder)
    texts = ["가중치 조절", "회의 일정", "가중치 조절"]
    first, ids = store.get(texts)
    assert first.shape == (3, 64) and first.dtype == np.float32 and ids[0] == ids[2]
    assert encoder.encoded == 2  # 같은 입력은 한 번만 계산
    second, _ = store.get(texts)
    assert encoder.encoded == 2 and np.array_equal(first, second)
    row = database.query_one("SELECT * FROM embeddings LIMIT 1")
    assert row["dtype"] == "float32" and row["dimension"] == 64 and row["config_hash"] == encoder.config_hash
    shard = tmp_path / row["file_path"]
    shard.write_bytes(b"truncated")  # 불완전한 shard
    fresh = EmbeddingStore(database, tmp_path, encoder)
    third, _ = fresh.get(texts)
    assert np.array_equal(first, third) and encoder.encoded == 4  # 손상된 shard는 쓰지 않고 다시 계산
    assert not list(tmp_path.rglob("*.part"))


def test_feature_modes_and_cache_separation_by_encoder(tmp_path):
    database = open_database(tmp_path / "db.sqlite3")
    store = EmbeddingStore(database, tmp_path, FakeEncoder())
    items = [FeatureInput("대상 문장", title="제목", prev="이전 문장", next="다음 문장"), FeatureInput("문맥 없는 문장")]
    separate, _ = build_features(items, store, "separate")
    target_only, _ = build_features(items, store, "target_only")
    composed, _ = build_features(items, store, "composed", FakeTokenizer(), SegmentationConfig())
    assert separate.shape == (2, 128) and target_only.shape == (2, 64) and composed.shape == (2, 64)
    assert np.array_equal(separate[:, :64], target_only)  # 앞 절반은 대상 임베딩 그대로
    assert np.linalg.norm(separate[0, 64:]) == pytest.approx(1.0, abs=1e-5)
    assert not separate[1, 64:].any()  # 문맥이 없으면 0 벡터
    assert not np.allclose(composed[0], target_only[0])  # 결합 입력은 문맥이 섞인 다른 벡터

    class OtherEncoder(FakeEncoder):
        config_hash = "another-encoder-config"

    other = EmbeddingStore(database, tmp_path, OtherEncoder())
    other.get(["대상 문장"])
    assert other.stats == {"hits": 0, "computed": 1}  # 인코더 설정이 다르면 캐시를 공유하지 않는다


# ---------------------------------------------------------------- 설정 제약
@pytest.mark.parametrize("key, value, message", [
    ("runtime.device", "cuda", "cpu"),
    ("runtime.device", "mps", "cpu"),
    ("runtime.workers", 2, "workers"),
    ("paths.e5", "https://huggingface.co/intfloat/multilingual-e5-small", "원격"),
    ("policy.candidate_threshold", 0.6, "threshold"),
    ("laya.mode", "shadow", "laya"),
    ("ui.host", "0.0.0.0", "루프백"),
    ("features.mode", "magic", "features.mode"),
])
def test_config_rejects_spec_violations(tmp_path, key, value, message):
    with pytest.raises(ConfigError, match=message):
        make_config(tmp_path, **{key: value})


def test_default_config_file_loads_and_yaml_off_is_accepted(tmp_path):
    repo_config = Path(__file__).resolve().parents[2] / "config" / "default.yaml"
    config = load_config(repo_config)
    assert config.runtime.device == "cpu" and config.runtime.offline and config.laya.mode == "off"
    assert config.policy.candidate_threshold is None and config.ui.host == "127.0.0.1"
    assert config.model_dir == (repo_config.parent.parent / "models" / "multilingual-e5-small").resolve()
    path = tmp_path / "c.yaml"
    path.write_text("laya:\n  mode: off\n", encoding="utf-8")  # YAML은 off를 False로 읽는다
    assert load_config(path).laya.mode == "off"
    path.write_text("unknown_section: {}\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)
    assert validate(AppConfig()).features.mode == "separate"


def test_pip_input_files_are_ascii():
    # Python 3.11에 들어 있는 pip(24.0 이하)은 requirements 파일을 Windows 시스템 코드 페이지로 읽는다.
    # 한글 주석이 있으면 운영 PC에서 설치 명령이 UnicodeDecodeError로 실패한다.
    root = Path(__file__).resolve().parents[2]
    files = [root / "requirements.lock", *sorted((root / "requirements").glob("*.in"))]
    assert len(files) == 5  # requirements.lock, base, tokenizers, dev, laya(실험용)
    for requirements_file in files:
        assert requirements_file.read_bytes().isascii(), f"{requirements_file.name}에 ASCII가 아닌 문자가 있다"


# ---------------------------------------------------------------- 오프라인
def test_missing_model_fails_without_any_download_attempt(tmp_path):
    runtime.enter_offline_mode()
    before = len(runtime.network_attempts())
    with pytest.raises(ModelError, match="자동으로 내려받지 않습니다"):
        load_model_info(tmp_path / "models" / "multilingual-e5-small")
    (tmp_path / "m").mkdir()
    with pytest.raises(ModelError, match="manifest"):
        load_model_info(tmp_path / "m")
    assert len(runtime.network_attempts()) == before  # DNS·HTTP 시도가 없다


def test_network_guard_blocks_external_and_allows_loopback():
    runtime.enter_offline_mode()
    with pytest.raises(runtime.NetworkBlockedError):
        socket.getaddrinfo("huggingface.co", 443)
    with pytest.raises(runtime.NetworkBlockedError):
        socket.gethostbyname("pypi.org")
    assert socket.gethostbyname("127.0.0.1") == "127.0.0.1"
    with pytest.raises(runtime.NetworkBlockedError):
        socket.create_connection(("93.184.216.34", 80), timeout=1)
    stream = socket.socket()
    try:
        with pytest.raises(runtime.NetworkBlockedError):
            stream.connect(("93.184.216.34", 80))
        assert stream.connect_ex(("127.0.0.1", 9)) != 0  # 루프백은 가드를 지나 OS가 거절한다
    finally:
        stream.close()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        with pytest.raises(runtime.NetworkBlockedError):
            client.sendto(b"x", ("8.8.8.8", 53))
    finally:
        client.close()
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        socket.create_connection(server.getsockname(), timeout=2).close()  # 루프백은 허용 (로컬 UI)
    finally:
        server.close()
    kinds = [attempt["kind"] for attempt in runtime.network_attempts()]
    assert {"dns", "connect", "sendto"} <= set(kinds)


def test_model_manifest_hash_mismatch_is_rejected(tmp_path):
    import json

    model_dir = tmp_path / "model"
    model_dir.mkdir()
    for name in ("model.onnx", "tokenizer.json", "config.json"):
        (model_dir / name).write_bytes(name.encode())
    files = {name: {"sha256": runtime.sha256_file(model_dir / name)} for name in ("model.onnx", "tokenizer.json", "config.json")}
    manifest = {"model_id": "x", "revision": "r", "license": "mit", "files": files,
                "embedding": {"dimension": 384, "max_tokens": 512}}
    (model_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert load_model_info(model_dir).dimension == 384
    (model_dir / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(ModelError, match="해시 불일치"):
        load_model_info(model_dir)


def test_segmentation_unit_is_validated_and_changes_bundle_compatibility(tmp_path):
    from patent_marker.classifiers.bundle import ModelBundle, compatibility_problems
    from patent_marker.versions import segmentation_version

    config = AppConfig()
    config.segmentation.unit = "fine"
    assert validate(config).segmentation.unit == "fine"
    config.segmentation.unit = "sentence"
    with pytest.raises(ConfigError, match="segmentation.unit"):
        validate(config)
    path = tmp_path / "config.yaml"
    path.write_text("segmentation:\n  unit: fine\n", encoding="utf-8")
    assert load_config(path).segmentation.unit == "fine"

    assert segmentation_version("paragraph") == "segmentation-1" and segmentation_version("fine") == "segmentation-2-fine"
    bundle = ModelBundle(model_version="classifier-0001", kind="trained", coef=[0.5], intercept=0.1,
                         feature_config={"encoder_config_hash": "enc-hash"}, feature_config_hash="f",
                         encoder_revision="r", encoder_config_hash="enc-hash", tokenizer_hash="t", created_at="now",
                         segmentation_version=segmentation_version("fine"))
    assert compatibility_problems(bundle, "enc-hash", segmentation_version("fine")) == []
    assert any("분할 버전" in problem for problem in compatibility_problems(bundle, "enc-hash"))
