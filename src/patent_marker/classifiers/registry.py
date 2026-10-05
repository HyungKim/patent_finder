"""모델 registry: 등록, 원자적 승격, 롤백 (스펙 10절).

운영 모델은 system_state['active_model'] 한 곳이 가리킨다. 승격·롤백은 하나의 transaction에서
이전 모델 은퇴와 새 모델 지정을 함께 처리한다. 진행 중인 분석은 시작 시점의 bundle을 메모리에
들고 끝까지 쓰므로 영향을 받지 않는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..runtime import utc_now
from ..storage import Database
from .bundle import ModelBundle, load_bundle

STAGE_SEED = "SEED"
STAGE_PILOT = "PILOT"
STAGE_PRODUCTION = "PRODUCTION"
SEED_MODEL_VERSION = "classifier-0000"


class RegistryError(RuntimeError):
    pass


def next_model_version(database: Database) -> str:
    row = database.query_one(
        "SELECT MAX(CAST(SUBSTR(model_version, 12) AS INTEGER)) FROM model_registry WHERE model_version LIKE 'classifier-%'"
    )
    return f"classifier-{(row[0] if row and row[0] is not None else 0) + 1:04d}"


def register_model(database: Database, bundle: ModelBundle, artifact_path: Path, artifact_sha256: str,
                   base_dir: Path, training_run_id: str | None, policy_version: str | None) -> None:
    try:
        stored = str(Path(artifact_path).resolve().relative_to(base_dir.resolve()))
    except ValueError:
        stored = str(artifact_path)
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO model_registry (model_version, kind, artifact_path, artifact_sha256, training_run_id, "
            "encoder_revision, policy_version, stage, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (bundle.model_version, bundle.kind, stored, artifact_sha256, training_run_id,
             bundle.encoder_revision, policy_version, utc_now()),
        )


def get_model(database: Database, model_version: str) -> dict[str, Any] | None:
    row = database.query_one("SELECT * FROM model_registry WHERE model_version = ?", (model_version,))
    return dict(row) if row else None


def load_registered_bundle(database: Database, model_version: str, base_dir: Path) -> ModelBundle:
    """registry의 해시와 일치하는 bundle만 로드한다."""
    row = get_model(database, model_version)
    if row is None:
        raise RegistryError(f"registry에 없는 모델입니다: {model_version}")
    path = Path(row["artifact_path"])
    if not path.is_absolute():
        path = base_dir / path
    return load_bundle(path, expected_sha256=row["artifact_sha256"])


def get_active(database: Database) -> dict[str, Any] | None:
    """현재 운영 모델: {"model_version", "policy_version", "stage"} 또는 None."""
    row = database.query_one("SELECT value FROM system_state WHERE key = 'active_model'")
    if row is None:
        return None
    value = json.loads(row["value"])
    return value or None


def set_active(database: Database, model_version: str, policy_version: str | None, stage: str, action: str,
               report_path: str | None = None, reason: str | None = None,
               details: dict[str, Any] | None = None) -> None:
    """운영 모델을 원자적으로 교체하고 이력을 남긴다."""
    now = utc_now()
    with database.transaction() as connection:
        if connection.execute("SELECT 1 FROM model_registry WHERE model_version = ?", (model_version,)).fetchone() is None:
            raise RegistryError(f"registry에 없는 모델입니다: {model_version}")
        previous = connection.execute("SELECT value FROM system_state WHERE key = 'active_model'").fetchone()
        if previous:
            old = json.loads(previous["value"]) or {}
            if old.get("model_version") and old["model_version"] != model_version:
                connection.execute("UPDATE model_registry SET retired_at = ? WHERE model_version = ?",
                                   (now, old["model_version"]))
        connection.execute(
            "UPDATE model_registry SET promoted_at = ?, retired_at = NULL, stage = ?, policy_version = ? "
            "WHERE model_version = ?", (now, stage, policy_version, model_version),
        )
        value = json.dumps({"model_version": model_version, "policy_version": policy_version, "stage": stage})
        connection.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('active_model', ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (value, now),
        )
        connection.execute(
            "INSERT INTO promotion_events (model_version, policy_version, action, stage, report_path, reason, "
            "details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (model_version, policy_version, action, stage, report_path, reason,
             json.dumps(details or {}, ensure_ascii=False), now),
        )


def record_blocked(database: Database, model_version: str, policy_version: str | None, stage: str,
                   report_path: str | None, reasons: list[str]) -> None:
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO promotion_events (model_version, policy_version, action, stage, report_path, reason, "
            "details_json, created_at) VALUES (?, ?, 'BLOCKED', ?, ?, ?, ?, ?)",
            (model_version, policy_version, stage, report_path, "; ".join(reasons),
             json.dumps({"reasons": reasons}, ensure_ascii=False), utc_now()),
        )
