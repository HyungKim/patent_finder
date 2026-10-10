"""판정으로 다시 학습하고, 평가해 보여 준 뒤, 사람이 답하면 운영 모델을 바꾸는 한 번의 흐름 (train.bat의 본체).

snapshot → train → evaluate → 지금 모델과 비교 → 교체 여부 확인. 각 단계는 같은 이름의 명령과 같은 함수를 쓴다.
운영 모델은 사람이 '예'라고 답했을 때만 바뀐다. 판정이 쌓였다고 저절로 바꾸지 않는다 (스펙 12.4).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .classifiers.registry import STAGE_PRODUCTION
from .classifiers.train import TrainingBlocked
from .evaluation.reports import EvaluationError, evaluate_model
from .feedback.snapshot import SnapshotError, create_snapshot
from .promotion import PromotionBlocked, check_promotion, promote
from .services import Services
from .training import train_from_snapshot
from .ui.server import ReviewApp

DEFAULT_PILOT_REASON = "retrain(train.bat)에서 시범 사용으로 승격"
YES_ANSWERS = ("y", "yes", "예", "네", "ㅇ")


class RetrainBlocked(RuntimeError):
    """더 진행할 수 없는 상태. 메시지를 그대로 사용자에게 보여 준다."""


def _fmt(value: Any) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _row(label: str, model_version: str, report: dict[str, Any]) -> str:
    at = (report.get("segment_level") or {}).get("at_threshold") or {}
    end = report.get("end_to_end") or {}
    missed = int(at.get("fn") or 0) + int(end.get("fn_unparsed") or 0)
    recall = end.get("recall", at.get("recall"))
    return (f"  {label:10s}{model_version:17s}{int(at.get('tp') or 0):5d}{missed:6d}{int(at.get('fp') or 0):7d}"
            f"{_fmt(recall):>9s}{_fmt(at.get('precision')):>11s}{_fmt(at.get('review_ratio')):>10s}")


def _unique_name(database: Any, base: str) -> str:
    """같은 초에 두 번 실행해도 snapshot 이름이 겹치지 않게 한다 (snapshot은 덮어쓰지 않는다)."""
    name, number = base, 2
    while database.query_one("SELECT 1 FROM label_snapshots WHERE snapshot_id = ?", (name,)) is not None:
        name, number = f"{base}-{number}", number + 1
    return name


def retrain(services: Services, *, name: str | None = None, decision: str = "ask", stage: str = "auto",
            reason: str | None = None, say: Callable[[str], None] | None = None,
            ask: Callable[[str], str] | None = None) -> dict[str, Any]:
    """decision: ask(화면에서 묻는다) / yes / no. stage: auto(조건을 채우면 production, 아니면 pilot) / pilot / production.

    반환: 단계별 결과. 운영 모델을 바꿨으면 promoted=True.
    """
    say = say or (lambda message: None)
    config, database = services.config, services.database
    status = ReviewApp(database, config).status()
    labels = status["counts"]["labels"]
    yes, no, hold = labels.get("YES", 0), labels.get("NO", 0), labels.get("HOLD", 0)
    active = status["model"]
    say(f"[1/4] 판정 현황: YES {yes}개, NO {no}개, 보류 {hold}개 · 마지막 학습 뒤 새 판정 {status['retraining']['new_labels']}개")
    if active:
        say(f"      지금 운영 모델: {active['model_version']} ({active.get('stage')})")
    if yes == 0 or no == 0:
        raise RetrainBlocked(
            f"YES와 NO 판정이 모두 있어야 학습할 수 있습니다 (YES {yes}개, NO {no}개). run.bat의 검토 화면에서 판정을 더 남기세요."
        )
    name = _unique_name(database, name or f"labels-{datetime.now():%Y%m%d-%H%M%S}")
    try:
        snapshot = create_snapshot(database, config, name)
    except SnapshotError as exc:
        raise RetrainBlocked(str(exc)) from exc
    stats, parts = snapshot["stats"], snapshot["stats"]["partitions"]
    groups = {key: parts.get(key, {}).get("groups", 0) for key in ("train", "validation", "test")}
    say(f"      snapshot {name}: 판정 {stats['labels']}개, 문서 계열 {stats['families']}개 "
        f"(학습용 {groups['train']}, 검증용 {groups['validation']}, 시험용 {groups['test']} 계열)")

    say("[2/4] 학습")
    try:
        trained = train_from_snapshot(services, Path(snapshot["path"]))
    except TrainingBlocked as exc:
        raise RetrainBlocked(str(exc)) from exc
    model, human = trained["model_version"], trained["human_labels"]
    threshold = "없음" if trained["threshold"] is None else f"{trained['threshold']:.3f}"
    say(f"      새 모델 {model} · 학습에 쓴 판정 {human['train']}개 (YES {human['yes']}, NO {human['no']}) "
        f"· 표시 기준값 {threshold} ({trained['policy_status']})")
    if trained["experimental"]:
        say("      판정이 아직 적어 실험 모델입니다 (판정 200개, YES·NO 각 50개 미만).")

    say("[3/4] 평가")
    report, split = None, None
    for partition in ("test", "validation"):
        try:
            report = evaluate_model(services, model, f"{partition}-{trained['split_tag']}")
            split = partition
            break
        except EvaluationError as exc:
            say(f"      {partition} 문서로는 평가하지 못함: {exc}")
    if report is None:
        raise RetrainBlocked(
            "새 모델을 평가할 문서가 없습니다. 학습에 쓰지 않은 문서의 판정이 있어야 새 모델을 확인하고 쓸 수 있습니다. "
            "문서를 더 판정한 뒤 다시 실행하세요."
        )
    comparison = None
    if active and active["model_version"] != model:
        try:
            comparison = evaluate_model(services, active["model_version"], f"{split}-{trained['split_tag']}")
        except Exception as exc:  # noqa: BLE001 - 비교는 보조 정보라 실패해도 진행한다
            say(f"      지금 모델과의 비교는 건너뜀: {type(exc).__name__}: {exc}")
    counts = report["counts"]
    say(f"      {'시험용' if split == 'test' else '검증용'} 문서 {counts['documents']}개의 판정 {counts['segments']}개"
        f"(진짜 후보 {counts['positive_segments']}개)로 평가")
    say(f"  {'':10s}{'':17s}{'찾음':>5s}{'놓침':>6s}{'헛표시':>7s}{'Recall':>9s}{'Precision':>11s}{'검토 비율':>10s}")
    if comparison:
        say(_row("지금 모델", active["model_version"], comparison))
    say(_row("새 모델", model, report))
    if report["threshold_selected_on_this_partition"]:
        say("      기준값을 고른 문서와 같은 문서로 평가했으므로 수치가 실제보다 좋게 나옵니다.")

    say("[4/4] 운영 모델 교체")
    blockers, _details = check_promotion(services, model, Path(report["report_path"]), STAGE_PRODUCTION)
    if stage == "production" or (stage == "auto" and not blockers):
        chosen = "production"
    else:
        chosen = "pilot"
        if stage == "auto":
            say("      정식(PRODUCTION) 승격 조건에 못 미쳐 시범(PILOT)으로 올립니다. 결과물에 PILOT이 표시됩니다.")
            for item in blockers:
                say(f"        - {item}")
    if decision == "ask":
        try:
            answer = (ask or input)(f"      새 모델 {model}을(를) 지금부터 쓰시겠습니까? [y/N] ")
        except EOFError:
            answer = ""
        decision = "yes" if answer.strip().lower() in YES_ANSWERS else "no"
    promoted = None
    if decision == "yes":
        try:
            promoted = promote(services, model, Path(report["report_path"]), chosen,
                               (reason or DEFAULT_PILOT_REASON) if chosen == "pilot" else reason)
        except PromotionBlocked as exc:
            raise RetrainBlocked("승격 차단: " + " / ".join(exc.reasons)) from exc
        say(f"      {model}을(를) {promoted['stage']} 단계의 운영 모델로 바꿨습니다. 다음 분석(mark.bat)부터 새 모델을 씁니다.")
        if active:
            say(f"      되돌리기: python -m patent_marker.cli rollback --model {active['model_version']}")
    else:
        say(f"      운영 모델은 그대로입니다{' (' + active['model_version'] + ')' if active else ''}.")
        say(f"      나중에 쓰려면: python -m patent_marker.cli promote --model {model} --report {report['report_path']}"
            + (' --stage pilot --reason "사유"' if chosen == "pilot" else ""))
    return {
        "snapshot": name, "model_version": model, "policy_status": trained["policy_status"],
        "split": split, "report_path": report["report_path"], "comparison": comparison, "stage": chosen,
        "production_blockers": blockers, "promoted": promoted is not None, "active": promoted,
    }
