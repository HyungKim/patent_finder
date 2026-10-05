"""설정 로드와 검증. 스펙 2절의 필수 제약(CPU, 오프라인, 로컬 경로)을 여기서 강제한다."""
from __future__ import annotations

import dataclasses
import getpass
import ipaddress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .runtime import canonical_json, sha256_text

FEATURE_MODES = ("separate", "target_only", "composed")


class ConfigError(ValueError):
    """설정이 스펙의 제약을 위반하거나 형식이 잘못됨."""


@dataclass
class RuntimeConfig:
    device: str = "cpu"
    cpu_threads: int = 4
    workers: int = 1
    batch_size: int = 8
    offline: bool = True
    process_tree_memory_budget_gib: float = 8.0


@dataclass
class PathsConfig:
    base_dir: str = ".."
    e5: str = "models/multilingual-e5-small"
    database: str = "data/patent_marker.sqlite3"
    data_dir: str = "data"
    artifacts_dir: str = "artifacts"
    outputs_dir: str = "outputs"


@dataclass
class ParsingConfig:
    include_speaker_notes: bool = False
    include_headers_footers: bool = False
    include_footnotes: bool = False
    include_hidden_slides: bool = True


@dataclass
class LimitsConfig:
    max_file_mb: int = 200
    max_units: int = 2000
    max_uncompressed_mb: int = 1024


@dataclass
class SegmentationConfig:
    max_input_tokens: int = 512
    target_tokens: int = 320
    context_tokens: int = 128
    overlap_tokens: int = 48
    title_tokens: int = 32
    short_item_tokens: int = 64


@dataclass
class EmbeddingConfig:
    prefix: str = "query: "
    normalize: bool = True
    pooling: str = "mean"


@dataclass
class FeaturesConfig:
    mode: str = "separate"


@dataclass
class ClassifierConfig:
    type: str = "logistic_regression"
    C: float = 1.0
    class_weight: str | None = "balanced"
    max_iter: int = 1000
    random_seed: int = 42


@dataclass
class SeedConfig:
    enabled: bool = True
    sample_weight: float = 0.3


@dataclass
class PolicyConfig:
    candidate_threshold: float | None = None
    status: str = "UNVALIDATED"
    recall_target: float = 0.95
    min_validation_positives: int = 10


@dataclass
class EvaluationConfig:
    split_ratios: list[float] = field(default_factory=lambda: [0.6, 0.2, 0.2])
    bootstrap_samples: int = 1000
    cv_folds: int = 5


@dataclass
class PromotionConfig:
    recall_target: float = 0.95
    min_positive_segments: int = 100
    min_document_families: int = 20


@dataclass
class RetrainingConfig:
    suggest_after_new_labels: int = 50


@dataclass
class ReviewConfig:
    reviewer_id: str | None = None
    label_guideline_version: str = "1.0"


@dataclass
class LayaConfig:
    mode: str = "off"
    local_path: str | None = None
    timeout_seconds: int = 30


@dataclass
class UiConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass
class ExportConfig:
    formats: list[str] = field(default_factory=lambda: ["html", "jsonl", "annotated"])
    pptx_summary_slide: bool = True


@dataclass
class AppConfig:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    parsing: ParsingConfig = field(default_factory=ParsingConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    seed: SeedConfig = field(default_factory=SeedConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    evaluation: EvaluationConfig = field(default_factory=EvaluationConfig)
    promotion: PromotionConfig = field(default_factory=PromotionConfig)
    retraining: RetrainingConfig = field(default_factory=RetrainingConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    laya: LayaConfig = field(default_factory=LayaConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    # 설정 파일 위치(경로 resolve 기준). 파일 없이 만든 설정은 현재 디렉터리 기준.
    config_dir: Path = field(default_factory=Path.cwd)

    # ---- 경로 ----
    @property
    def base_dir(self) -> Path:
        return (self.config_dir / self.paths.base_dir).resolve()

    def resolve(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else (self.base_dir / path).resolve()

    @property
    def model_dir(self) -> Path:
        return self.resolve(self.paths.e5)

    @property
    def database_path(self) -> Path:
        return self.resolve(self.paths.database)

    @property
    def data_dir(self) -> Path:
        return self.resolve(self.paths.data_dir)

    @property
    def artifacts_dir(self) -> Path:
        return self.resolve(self.paths.artifacts_dir)

    @property
    def outputs_dir(self) -> Path:
        return self.resolve(self.paths.outputs_dir)

    @property
    def reviewer_id(self) -> str:
        return self.review.reviewer_id or getpass.getuser()

    # ---- 해시 ----
    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data.pop("config_dir")
        return data

    def config_hash(self) -> str:
        """기기마다 달라지는 경로·UI 설정을 제외한 설정 해시."""
        data = self.to_dict()
        for key in ("paths", "ui"):
            data.pop(key, None)
        return sha256_text(canonical_json(data))

    def parse_options_hash(self) -> str:
        return sha256_text(canonical_json({
            "parsing": dataclasses.asdict(self.parsing),
            "segmentation": dataclasses.asdict(self.segmentation),
        }))[:16]


def _build(cls: type, raw: Any, section: str) -> Any:
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise ConfigError(f"설정 '{section}' 항목은 매핑이어야 합니다.")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(f"설정 '{section}'에 알 수 없는 키: {', '.join(unknown)}")
    return cls(**raw)


def _looks_remote(value: str) -> bool:
    return "://" in value or value.startswith(("hf:", "s3:", "gs:"))


def validate(config: AppConfig) -> AppConfig:
    rt = config.runtime
    if str(rt.device).lower() != "cpu":
        raise ConfigError("runtime.device는 'cpu'만 허용됩니다 (GPU/MPS 가속 금지).")
    rt.device = "cpu"
    if rt.workers != 1:
        raise ConfigError("runtime.workers는 현재 1만 지원합니다.")
    if rt.cpu_threads < 1 or rt.batch_size < 1:
        raise ConfigError("runtime.cpu_threads와 batch_size는 1 이상이어야 합니다.")
    if rt.process_tree_memory_budget_gib <= 0:
        raise ConfigError("runtime.process_tree_memory_budget_gib는 0보다 커야 합니다.")

    for name, value in dataclasses.asdict(config.paths).items():
        if not isinstance(value, str) or not value:
            raise ConfigError(f"paths.{name}는 비어 있지 않은 문자열이어야 합니다.")
        if _looks_remote(value):
            raise ConfigError(f"paths.{name}에 원격 주소를 쓸 수 없습니다: {value}")

    seg = config.segmentation
    if not (0 < seg.target_tokens < seg.max_input_tokens):
        raise ConfigError("segmentation.target_tokens는 max_input_tokens보다 작아야 합니다.")
    if not (0 <= seg.overlap_tokens < seg.target_tokens):
        raise ConfigError("segmentation.overlap_tokens는 target_tokens보다 작아야 합니다.")
    if seg.context_tokens < 0 or seg.title_tokens < 0:
        raise ConfigError("segmentation.context_tokens/title_tokens는 0 이상이어야 합니다.")

    if config.embedding.pooling != "mean":
        raise ConfigError("embedding.pooling은 'mean'만 지원합니다.")
    if config.features.mode not in FEATURE_MODES:
        raise ConfigError(f"features.mode는 {FEATURE_MODES} 중 하나여야 합니다.")
    if config.classifier.type != "logistic_regression":
        raise ConfigError("classifier.type은 'logistic_regression'만 지원합니다.")
    if config.classifier.class_weight not in (None, "balanced"):
        raise ConfigError("classifier.class_weight는 'balanced' 또는 null이어야 합니다.")
    if not (0 < config.seed.sample_weight <= 1):
        raise ConfigError("seed.sample_weight는 (0, 1] 범위여야 합니다.")

    if config.policy.candidate_threshold is not None:
        raise ConfigError(
            "policy.candidate_threshold는 설정 파일에서 지정할 수 없습니다. "
            "threshold는 train/evaluate가 만든 정책 버전으로만 관리됩니다."
        )
    for name in ("recall_target",):
        if not (0 < getattr(config.policy, name) <= 1):
            raise ConfigError(f"policy.{name}는 (0, 1] 범위여야 합니다.")

    ratios = config.evaluation.split_ratios
    if len(ratios) != 3 or any(r <= 0 for r in ratios) or abs(sum(ratios) - 1.0) > 1e-6:
        raise ConfigError("evaluation.split_ratios는 합이 1인 양수 3개여야 합니다.")

    # YAML 1.1은 따옴표 없는 off를 False로 읽는다 (스펙 14.2 예시 호환).
    if config.laya.mode is False or config.laya.mode is None:
        config.laya.mode = "off"
    if config.laya.mode != "off":
        raise ConfigError("laya.mode는 현재 'off'만 지원합니다 (shadow/assist/filter는 Phase 3 이후 미구현).")

    host = config.ui.host
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ConfigError("ui.host는 루프백 주소(127.0.0.1)만 허용됩니다.")

    allowed_formats = {"html", "jsonl", "annotated"}
    bad = sorted(set(config.export.formats) - allowed_formats)
    if bad:
        raise ConfigError(f"export.formats에 지원하지 않는 형식: {', '.join(bad)}")
    return config


def load_config(path: str | Path | None = None) -> AppConfig:
    """YAML 설정을 읽는다. path가 None이면 ./config/default.yaml을 찾는다."""
    if path is None:
        path = Path.cwd() / "config" / "default.yaml"
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"설정 파일 형식 오류: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("설정 파일 최상위는 매핑이어야 합니다.")
    sections = {f.name: f for f in dataclasses.fields(AppConfig) if f.name != "config_dir"}
    unknown = sorted(set(raw) - set(sections))
    if unknown:
        raise ConfigError(f"알 수 없는 설정 섹션: {', '.join(unknown)}")
    values = {}
    for name, fld in sections.items():
        section_cls = fld.default_factory  # type: ignore[misc]
        values[name] = _build(section_cls, raw.get(name), name)
    config = AppConfig(config_dir=path.resolve().parent, **values)
    return validate(config)
