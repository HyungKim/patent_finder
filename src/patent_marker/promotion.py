"""승격 게이트와 롤백 (스펙 10·12.4·14.3).

promote는 평가 기준을 못 채운 모델을 자동으로 막는다. 누수가 있거나 threshold가 검증되지 않은
모델을 통과로 처리하지 않는다. 목표에 못 미치는 모델은 사유를 남기고 PILOT 단계로만 올릴 수 있으며,
그 경우 모든 출력에 PILOT이 표시된다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .classifiers.bundle import BundleError
from .classifiers.registry import (STAGE_PILOT, STAGE_PRODUCTION, STAGE_SEED, RegistryError, get_active, get_model,
                                   load_registered_bundle, record_blocked, set_active)
from .policies.thresholds import CV_ESTIMATE, VALIDATED, get_policy
from .runtime import sha256_file
from .scoring import IncompatibleBundle, ensure_compatible
from .services import Services

STAGES = {"production": STAGE_PRODUCTION, "pilot": STAGE_PILOT}


class PromotionBlocked(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("승격 차단: " + " / ".join(reasons))
        self.reasons = reasons


def check_promotion(services: Services, model_version: str, report_path: Path, stage: str) -> tuple[list[str], dict[str, Any]]:
    """승격을 막는 사유 목록과 판단에 쓴 수치."""
    database, config = services.database, services.config
    reasons: list[str] = []
    details: dict[str, Any] = {"stage": stage}
    registered = get_model(database, model_version)
    if registered is None:
        return [f"registry에 없는 모델입니다: {model_version}"], details
    try:
        bundle = load_registered_bundle(database, model_version, config.base_dir)
        ensure_compatible(services, bundle)
    except (BundleError, IncompatibleBundle, RegistryError) as exc:
        return [str(exc)], details

    report_path = Path(report_path)
    if not report_path.is_file():
        return [f"평가 보고서가 없습니다: {report_path}"], details
    evaluation = database.query_one("SELECT * FROM evaluations WHERE report_sha256 = ?", (sha256_file(report_path),))
    if evaluation is None:
        return ["등록되지 않았거나 내용이 바뀐 평가 보고서입니다. evaluate를 다시 실행하세요."], details
    report = json.loads(report_path.read_text(encoding="utf-8"))
    policy = get_policy(database, registered["policy_version"])

    if report.get("model_version") != model_version or evaluation["model_version"] != model_version:
        reasons.append("평가 보고서가 다른 모델의 것입니다.")
    if report.get("artifact_sha256") != registered["artifact_sha256"]:
        reasons.append("평가 이후 분류기 파일이 바뀌었습니다.")
    if report.get("policy_version") != registered["policy_version"]:
        reasons.append("평가 이후 threshold 정책이 바뀌었습니다. 다시 평가하세요.")
    if not report.get("leakage", {}).get("ok"):
        reasons.append("평가 집합이 학습 데이터와 겹칩니다(누수). 독립된 partition으로 평가하세요.")
    if policy is None or policy["threshold"] is None:
        reasons.append("threshold가 없습니다(UNVALIDATED).")

    at = (report.get("segment_level") or {}).get("at_threshold") or {}
    end_to_end = report.get("end_to_end") or {}
    counts = report.get("counts", {})
    completeness = report.get("label_completeness", {})
    recall = end_to_end.get("recall", at.get("recall"))
    details.update({
        "recall": recall, "recall_ci95": at.get("recall_ci95"), "precision": at.get("precision"),
        "review_ratio": at.get("review_ratio"), "positive_segments": counts.get("positive_segments"),
        "document_families": counts.get("document_families"), "partition": report.get("split", {}).get("partition"),
        "policy_status": policy["status"] if policy else None, "experimental": bundle.training.get("experimental"),
    })

    if stage == STAGE_PRODUCTION:
        rule = config.promotion
        if not report.get("independent"):
            reasons.append("운영 승격에는 독립 test partition의 평가 보고서가 필요합니다.")
        if policy is not None and policy["status"] != VALIDATED:
            reasons.append(f"threshold가 validation partition에서 검증되지 않았습니다({policy['status']}).")
        if recall is None or recall < rule.recall_target:
            reasons.append(f"end-to-end Recall {recall if recall is None else round(recall, 3)}이(가) "
                           f"목표 {rule.recall_target}에 못 미칩니다.")
        if (counts.get("positive_segments") or 0) < rule.min_positive_segments:
            reasons.append(f"평가 양성 구간 {counts.get('positive_segments')}개 < {rule.min_positive_segments}개 (파일럿 상태).")
        if (counts.get("document_families") or 0) < rule.min_document_families:
            reasons.append(f"평가 문서 계열 {counts.get('document_families')}개 < {rule.min_document_families}개 (파일럿 상태).")
        if completeness.get("fully_labeled_documents") != completeness.get("documents"):
            reasons.append("평가 문서 중 전체 검토되지 않은 문서가 있어 Recall을 모집단 추정으로 볼 수 없습니다.")
    else:
        if policy is not None and policy["status"] not in (VALIDATED, CV_ESTIMATE):
            reasons.append(f"파일럿 승격에는 검증 또는 교차검증으로 정한 threshold가 필요합니다({policy['status']}).")
    return reasons, details


def promote(services: Services, model_version: str, report_path: Path, stage: str = "production",
            reason: str | None = None) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError("stage는 production 또는 pilot이어야 합니다.")
    stage_value = STAGES[stage]
    reasons, details = check_promotion(services, model_version, Path(report_path), stage_value)
    if stage_value == STAGE_PILOT and not (reason or "").strip():
        reasons.append("파일럿 승격에는 --reason으로 사유를 남겨야 합니다.")
    registered = get_model(services.database, model_version)
    policy_version = registered["policy_version"] if registered else None
    if reasons:
        if registered:
            record_blocked(services.database, model_version, policy_version, stage_value, str(report_path), reasons)
        raise PromotionBlocked(reasons)
    set_active(services.database, model_version, policy_version, stage_value, "PROMOTE",
               report_path=str(report_path), reason=reason, details=details)
    return {"model_version": model_version, "policy_version": policy_version, "stage": stage_value, "details": details}


def rollback(services: Services, model_version: str, reason: str | None = None) -> dict[str, Any]:
    """이전에 운영했던 모델(또는 seed)과 그 정책·전처리 bundle로 되돌린다."""
    database = services.database
    registered = get_model(database, model_version)
    if registered is None:
        raise RegistryError(f"registry에 없는 모델입니다: {model_version}")
    if registered["promoted_at"] is None:
        raise RegistryError(f"{model_version}은(는) 운영된 적이 없어 롤백 대상이 아닙니다. promote를 사용하세요.")
    active = get_active(database)
    if active and active["model_version"] == model_version:
        raise RegistryError(f"{model_version}은(는) 이미 운영 중입니다.")
    bundle = load_registered_bundle(database, model_version, services.config.base_dir)
    ensure_compatible(services, bundle)
    stage = registered["stage"] or (STAGE_SEED if registered["kind"] == "seed" else STAGE_PILOT)
    set_active(database, model_version, registered["policy_version"], stage, "ROLLBACK", reason=reason,
               details={"from": active})
    return {"model_version": model_version, "policy_version": registered["policy_version"], "stage": stage,
            "previous": active}
