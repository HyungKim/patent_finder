"""명령줄 진입점 (스펙 14.3).

    python -m patent_marker.cli doctor --offline
    python -m patent_marker.cli analyze --input ./data/inbox --output ./outputs/run-001
    python -m patent_marker.cli mark 보고서.pptx 자료폴더
    python -m patent_marker.cli review --host 127.0.0.1

긴 작업은 진행 상황을 출력하고, 중단되면 같은 명령을 다시 실행해 이어서 진행할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import AppConfig, ConfigError, load_config

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED = 0, 1, 2


def _print(message: str = "") -> None:
    print(message, flush=True)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _services(config: AppConfig, need_encoder: bool = True):
    from .services import build_services

    return build_services(config, need_encoder=need_encoder)


# ====================================================================== doctor
def cmd_doctor(args: argparse.Namespace, config: AppConfig) -> int:
    """환경·의존성·로컬 모델·DB를 점검한다. 실패 항목이 있으면 종료 코드 1."""
    import importlib
    import importlib.util

    from .runtime import (MemoryMonitor, enter_offline_mode, network_attempts, network_guard_installed,
                          total_memory_gib)

    results: list[tuple[str, str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        results.append((name, status, detail))

    if args.offline or config.runtime.offline:
        enter_offline_mode()
    add("Python", "ok" if sys.version_info >= (3, 11) else "fail",
        f"{platform.python_version()} · {platform.platform()} · {platform.machine()}")
    for module, package in (("numpy", "numpy"), ("onnxruntime", "onnxruntime"), ("tokenizers", "tokenizers"),
                            ("sklearn", "scikit-learn"), ("docx", "python-docx"), ("pptx", "python-pptx"),
                            ("pdfplumber", "pdfplumber"), ("pypdf", "pypdf"), ("yaml", "PyYAML"), ("psutil", "psutil")):
        try:
            loaded = importlib.import_module(module)
            add(f"패키지 {package}", "ok", str(getattr(loaded, "__version__", "설치됨")))
        except Exception as exc:
            add(f"패키지 {package}", "fail", f"import 실패: {type(exc).__name__}")
    present = [name for name in ("huggingface_hub", "requests", "urllib3", "httpx", "torch", "transformers")
               if importlib.util.find_spec(name)]
    add("다운로드 가능 라이브러리", "warn" if present else "ok",
        f"설치되어 있음: {', '.join(present)} (운영 환경에서는 불필요)" if present else "설치되어 있지 않음")
    add("설정", "ok" if config.runtime.offline or not args.offline else "fail",
        f"device={config.runtime.device}, offline={config.runtime.offline}, threads={config.runtime.cpu_threads}, "
        f"features.mode={config.features.mode}, laya={config.laya.mode}")

    encoder = None
    try:
        from .embeddings.e5 import load_e5
        from .embeddings.manifest import load_model_info

        info = load_model_info(config.model_dir, verify_hashes=True)
        add("로컬 모델 파일", "ok", f"{info.model_id}@{info.revision[:12]} · 해시 일치 · {config.model_dir}")
        started = time.perf_counter()
        encoder, tokenizer = load_e5(config, memory=MemoryMonitor(config.runtime.process_tree_memory_budget_gib),
                                     verify_hashes=False)
        vectors = encoder.encode(["오프라인 점검 문장입니다.", "offline smoke test"])
        norms = [float((row ** 2).sum() ** 0.5) for row in vectors]
        good = (vectors.shape == (2, info.dimension) and str(vectors.dtype) == "float32"
                and all(abs(n - 1.0) < 1e-3 for n in norms))
        add("임베딩 추론", "ok" if good else "fail",
            f"shape={vectors.shape}, dtype={vectors.dtype}, provider={encoder.providers}, "
            f"로드+추론 {time.perf_counter() - started:.2f}s")
        add("실행 장치", "ok" if encoder.providers == ["CPUExecutionProvider"] else "fail", ", ".join(encoder.providers))
    except Exception as exc:
        add("로컬 모델", "fail", str(exc))

    if network_guard_installed():
        attempts = network_attempts()
        add("네트워크 차단 가드", "fail" if attempts else "ok",
            f"차단된 접속 시도 {len(attempts)}건: {attempts[:3]}" if attempts else "설치됨, 접속 시도 없음")
    else:
        add("네트워크 차단 가드", "warn", "설치되지 않음 (runtime.offline=false)")

    try:
        from .storage import Database

        database = Database(config.database_path)
        pending = database.pending_versions() if config.database_path.exists() else None
        if pending is None:
            add("DB", "ok", f"아직 없음. 첫 실행 때 생성: {config.database_path}")
        else:
            integrity = database.integrity()
            ok = not pending and integrity["integrity_ok"] and not integrity["foreign_key_violations"]
            add("DB", "ok" if ok else "warn" if pending else "fail",
                f"schema {database.applied_versions()} · 미적용 migration {pending} · 무결성 {integrity['integrity_ok']}")
            if not pending and encoder is not None:
                from .classifiers.bundle import compatibility_problems
                from .classifiers.registry import get_active, load_registered_bundle

                active = get_active(database)
                if active:
                    bundle = load_registered_bundle(database, active["model_version"], config.base_dir)
                    problems = compatibility_problems(bundle, encoder.config_hash)
                    add("운영 모델", "fail" if problems else "ok",
                        f"{active['model_version']} ({active.get('stage')})" + (": " + " / ".join(problems) if problems else ""))
                else:
                    add("운영 모델", "ok", "없음 (UNTRAINED 또는 첫 analyze 때 seed 생성)")
    except Exception as exc:
        add("DB", "fail", f"{type(exc).__name__}: {exc}")

    for label, directory in (("data", config.data_dir), ("artifacts", config.artifacts_dir), ("outputs", config.outputs_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            add(f"쓰기 권한 {label}", "ok", str(directory))
        except OSError as exc:
            add(f"쓰기 권한 {label}", "fail", f"{directory}: {exc}")

    memory = MemoryMonitor(config.runtime.process_tree_memory_budget_gib)
    total = total_memory_gib()
    add("메모리", "ok" if total >= config.runtime.process_tree_memory_budget_gib else "warn",
        f"물리 RAM {total}GiB · 예산 {config.runtime.process_tree_memory_budget_gib}GiB · 현재 RSS {memory.rss_bytes() / (1 << 30):.2f}GiB")
    cores = os.cpu_count() or 1
    add("CPU", "ok" if config.runtime.cpu_threads <= cores else "warn",
        f"논리 코어 {cores} · 설정 threads {config.runtime.cpu_threads} · {platform.processor() or platform.machine()}")

    marks = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    for name, status, detail in results:
        _print(f"{marks[status]} {name}: {detail}")
    failed = sum(status == "fail" for _, status, _ in results)
    _print(f"\n점검 {len(results)}개 · 실패 {failed}개 · 경고 {sum(status == 'warn' for _, status, _ in results)}개")
    return EXIT_ERROR if failed else EXIT_OK


# ====================================================================== 수집·분석
def cmd_ingest(args: argparse.Namespace, config: AppConfig) -> int:
    from .ingest import discover_files, ingest_file

    services = _services(config, need_encoder=False)
    files = discover_files(Path(args.input))
    problems = 0
    for index, path in enumerate(files, start=1):
        result = ingest_file(services, path, args.encoding)
        note = " (이미 수집됨)" if result.reused else ""
        _print(f"[{index}/{len(files)}] {result.status:11s} {path.name} · 문단 {result.paragraphs} · 구간 {result.segments}{note}"
               + (f" · {result.error}" if result.error else ""))
        problems += result.status in ("unsupported", "failed")
    _print(f"\n완료: {len(files)}개 중 미지원·실패 {problems}개. 검토 화면: python -m patent_marker.cli review")
    return EXIT_OK


def _print_export(result: dict[str, Any]) -> None:
    for name, entry in result["files"].items():
        _print(f"  {name}: {entry['path']}")
    for entry in result.get("annotated", []):
        if entry["status"] == "ok":
            cleared = sum(entry.get("cleared", {}).values())
            _print(f"  마킹 사본: {entry['output']} (표시 {entry['marked']}건, 건너뜀 {len(entry['skipped'])}건"
                   + (f", 기존 마킹 {cleared}건 제거" if cleared else "") + ")")
        elif entry["status"] != "no_marks":
            _print(f"  마킹 사본 없음: {entry['file_name']} · {entry['reason']}")
    for problem in result["problems"]:
        _print(f"  처리하지 못한 파일: {problem['file']} ({problem['status']}) · {problem['reason']}")


def cmd_analyze(args: argparse.Namespace, config: AppConfig) -> int:
    from .analysis import run_analysis
    from .export.runner import export_run

    output = Path(args.output).resolve()
    source = Path(args.input).resolve()
    if output == source or output == source.parent and source.is_file():
        _print("오류: 출력 경로가 입력 경로와 같습니다. 원본과 다른 디렉터리를 지정하세요.")
        return EXIT_ERROR
    run_id = args.run_id or output.name
    services = _services(config)
    summary = run_analysis(services, source, run_id, encoding=args.encoding, progress=_print)
    _print_run_summary(run_id, summary)
    formats = args.format.split(",") if args.format else None
    result = export_run(services.database, config, run_id, output, formats)
    _print("결과물:")
    _print_export(result)
    if summary["stage"] == "SEED":
        _print("\n주의: 합성 seed 분류기의 임시 결과입니다. 실제 문서에서의 Recall은 측정되지 않았습니다.")
    return EXIT_OK


def _print_run_summary(run_id: str, summary: dict[str, Any]) -> None:
    stage_text = {"SEED": "임시 후보(SEED · 미검증)", "PILOT": "파일럿 모델", "PRODUCTION": "운영 모델"}
    _print(f"\nrun {run_id}: {summary['status']} · 모델 {summary['model_version'] or '없음'}"
           f" ({stage_text.get(summary['stage'], 'UNTRAINED')}) · 구간 {summary['segments']}개")
    memory = summary["details"]["memory"]
    _print(f"처리 {summary['details']['elapsed_seconds']}s · peak RSS {memory['peak_rss_gib']}GiB"
           f" (예산 {memory['budget_gib']}GiB {'이내' if memory['within_budget'] else '초과'})")


def _open_folder(path: Path) -> None:
    """결과 폴더를 탐색기로 연다. Windows에서만 하고, PM_NO_OPEN이 있으면 건너뛴다."""
    if sys.platform != "win32" or os.environ.get("PM_NO_OPEN"):
        return
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


def cmd_mark(args: argparse.Namespace, config: AppConfig) -> int:
    """파일이나 폴더를 받아 바로 분석한다. mark.bat의 진입점이다(더블클릭해 열기 창에서 고르거나 끌어다 놓기)."""
    from .analysis import run_analysis
    from .dropped import recover_dropped
    from .export.runner import export_run

    chosen = recover_dropped(list(args.paths), os.environ.get("PM_CMDLINE"))
    if not chosen and args.pick:
        from .pickdialog import PickerUnavailable, pick_files

        try:
            chosen = pick_files()
        except PickerUnavailable as exc:
            _print(f"오류: {exc}")
            _print("분석할 파일이나 폴더를 mark.bat 아이콘 위에 끌어다 놓는 방법은 그대로 쓸 수 있습니다.")
            return EXIT_ERROR
        if not chosen:
            _print("파일을 고르지 않았습니다. 분석하지 않고 끝냅니다.")
            return EXIT_OK
    paths = [Path(item).resolve() for item in chosen]
    missing = [str(path) for path in paths if not path.exists()]
    if not paths or missing:
        _print("오류: 분석할 파일을 찾지 못했습니다." + (" 없는 경로: " + ", ".join(missing) if missing else ""))
        return EXIT_ERROR
    services = _services(config)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, number = f"mark-{stamp}", 2
    while services.database.query_one("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)) is not None:
        run_id, number = f"mark-{stamp}-{number}", number + 1
    output = config.outputs_dir / run_id
    summary = run_analysis(services, paths, run_id, encoding=args.encoding, progress=_print)
    _print_run_summary(run_id, summary)
    result = export_run(services.database, config, run_id, output, None)
    _print("결과물:")
    _print_export(result)
    if summary["stage"] == "SEED":
        _print("\n주의: 합성 seed 분류기의 임시 결과입니다. 실제 문서에서의 Recall은 측정되지 않았습니다.")
    _print(f"\n결과 폴더: {output}")
    if args.open:
        _open_folder(output)
    return EXIT_OK


def cmd_laya_compare(args: argparse.Namespace, config: AppConfig) -> int:
    """실험: 저장된 run의 1차 후보를 Laya로 다시 판단해 비교 자료를 만든다. 1차 결과와 기존 결과물은 바꾸지 않는다."""
    from .runtime import enter_offline_mode
    from .second_stage.compare import run_comparison
    from .second_stage.laya import DEFAULT_MODEL_DIR, LayaSecondStage, LayaUnavailable
    from .storage import open_database

    if not 0.0 <= args.agree_at <= 1.0:
        _print("오류: --agree-at은 0과 1 사이의 값이어야 합니다.")
        return EXIT_ERROR
    if config.runtime.offline:
        enter_offline_mode()
    database = open_database(config.database_path)
    if database.query_one("SELECT 1 FROM runs WHERE run_id = ?", (args.run,)) is None:
        _print(f"오류: run을 찾을 수 없습니다: {args.run}")  # 모델을 불러오기 전에 확인한다
        return EXIT_ERROR
    model_dir = config.resolve(config.laya.local_path or DEFAULT_MODEL_DIR)
    try:
        adapter = LayaSecondStage(model_dir, config.data_dir / "laya_runtime", threads=config.runtime.cpu_threads,
                                  agree_at=args.agree_at)
    except LayaUnavailable as exc:
        _print(f"오류: {exc}")
        return EXIT_ERROR
    _print(f"Laya 모델 {adapter.model_version} · 불러오는 데 {adapter.load_seconds:.1f}초")
    output = Path(args.output) if args.output else config.outputs_dir / args.run / "laya"
    summary = run_comparison(database, config, args.run, adapter, output, progress=_print)
    _print(f"1차 후보 {summary['candidates']}개 중 동의 {summary['agree']}개, 이견 {summary['disagree']}개, "
           f"판단 못 함 {summary['not_assessed']}개 · 판단에 {summary['assess_seconds']}초")
    _print(f"비교 자료: {summary['output_dir']}")
    _print("  laya_report.html      후보마다 Laya 점수와 판단")
    _print("  marked_laya_agree     Laya도 후보로 본 것만 표시한 사본 (비교용)")
    _print("\n주의: 실험 기능입니다. '동의' 기준은 검증되지 않았고, 1차 판정과 기존 결과물은 그대로입니다.")
    return EXIT_OK


def cmd_export(args: argparse.Namespace, config: AppConfig) -> int:
    from .export.runner import export_run
    from .runtime import enter_offline_mode
    from .storage import open_database

    if config.runtime.offline:
        enter_offline_mode()
    database = open_database(config.database_path)
    output = Path(args.output) if args.output else config.outputs_dir / args.run
    result = export_run(database, config, args.run, output, args.format.split(",") if args.format else None)
    _print(f"run {args.run} 결과물:")
    _print_export(result)
    return EXIT_OK


def cmd_review(args: argparse.Namespace, config: AppConfig) -> int:
    from .runtime import enter_offline_mode
    from .storage import open_database
    from .ui.server import create_server

    if config.runtime.offline:
        enter_offline_mode()
    database = open_database(config.database_path)
    server, _app = create_server(database, config, args.host, args.port)
    host, port = server.server_address[:2]
    _print(f"검토 화면: http://{host}:{port}/  (이 PC에서만 접속 가능 · 종료: Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _print("\n종료합니다.")
    finally:
        server.server_close()
    return EXIT_OK


# ====================================================================== 학습·평가·승격
def cmd_snapshot(args: argparse.Namespace, config: AppConfig) -> int:
    from .feedback.snapshot import create_snapshot
    from .storage import open_database

    database = open_database(config.database_path)
    result = create_snapshot(database, config, args.name, Path(args.output) if args.output else None)
    stats = result["stats"]
    _print(f"snapshot {result['snapshot_id']}: 라벨 {stats['labels']}개 (YES {stats['yes']}, NO {stats['no']},"
           f" 일괄 검토 NO {stats['implicit']}) · 문서 계열 {stats['families']}개")
    for name, counts in stats["partitions"].items():
        _print(f"  {name:10s} 계열 {counts['groups']:3d} · 라벨 {counts['n']:5d} · YES {counts['pos']:4d}")
    _print(f"파일: {result['path']}\n학습: python -m patent_marker.cli train --snapshot {result['path']}")
    return EXIT_OK


def cmd_train(args: argparse.Namespace, config: AppConfig) -> int:
    from .classifiers.train import TrainingBlocked
    from .training import train_from_snapshot

    services = _services(config)
    try:
        result = train_from_snapshot(services, Path(args.snapshot))
    except TrainingBlocked as exc:
        _print(f"학습 차단: {exc}")
        return EXIT_BLOCKED
    labels = result["human_labels"]
    _print(f"{result['model_version']} 학습 완료 (운영 모델은 바뀌지 않았습니다)")
    _print(f"  사람 라벨: train {labels['train']}개 (YES {labels['yes']}, NO {labels['no']}), validation {labels['validation']}개")
    if result["seed"]:
        _print(f"  seed 예문 {result['seed']['examples']}개를 함께 사용 (가중치 {result['seed']['sample_weight']})")
    if not result["converged"]:
        _print("  경고: 최적화가 수렴하지 않았습니다. classifier.max_iter를 늘려 다시 학습하세요.")
    if result["experimental"]:
        _print("  실험 모델: 라벨 수가 초기 수집 계획(200개, 클래스별 50개)에 못 미칩니다.")
    selection = result["selection"]
    if result["threshold"] is None:
        _print(f"  threshold 없음 (UNVALIDATED): {selection.get('reason', '')}")
    else:
        _print(f"  정책 {result['policy_version']} ({result['policy_status']}): threshold {result['threshold']:.4f}"
               f" · 선택 기준 {selection['basis']} · Recall {selection['recall']:.3f}"
               f" · 검토 비율 {selection['review_ratio']:.3f} (양성 {selection['n_positive']}개)")
    _print(f"다음: python -m patent_marker.cli evaluate --model {result['model_version']} --split test-{result['split_tag']}")
    return EXIT_OK


def cmd_experiment(args: argparse.Namespace, config: AppConfig) -> int:
    from .classifiers.train import TrainingBlocked
    from .runtime import atomic_write_json
    from .training import compare_feature_modes

    services = _services(config)
    modes = args.modes.split(",")
    c_values = [float(value) for value in args.c_values.split(",")] if args.c_values else None
    try:
        report = compare_feature_modes(services, Path(args.snapshot), modes, c_values)
    except TrainingBlocked as exc:
        _print(f"실험 불가: {exc}")
        return EXIT_BLOCKED
    _print(f"그룹 교차검증 비교 ({report['basis']}, Recall 목표 {report['recall_target']})")
    _print(f"{'mode':12s} {'C':>6s} {'AP':>7s} {'Recall':>7s} {'Prec.':>7s} {'검토비율':>8s} {'n':>5s} {'양성':>4s}")
    for row in report["results"]:
        def fmt(value: Any) -> str:
            return "N/A" if value is None else f"{value:.3f}"
        _print(f"{row['mode']:12s} {row['C']:6.2f} {fmt(row['average_precision']):>7s} {fmt(row['recall_at_threshold']):>7s}"
               f" {fmt(row['precision_at_threshold']):>7s} {fmt(row['review_ratio_at_threshold']):>8s}"
               f" {row['n']:5d} {row['n_positive']:4d}")
    path = config.artifacts_dir / f"experiment-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    atomic_write_json(path, report)
    _print(f"보고서: {path}\n같은 Recall에서 검토 비율이 낮은 구성이 유리합니다. 채택하려면 설정을 바꿔 train 하세요.")
    return EXIT_OK


def cmd_evaluate(args: argparse.Namespace, config: AppConfig) -> int:
    from .evaluation.reports import evaluate_model, render_markdown

    services = _services(config)
    report = evaluate_model(services, args.model, args.split, Path(args.output) if args.output else None)
    _print(render_markdown(report))
    _print(f"보고서: {report['report_path']}")
    if report["threshold_selected_on_this_partition"]:
        _print("주의: 이 partition에서 threshold를 골랐으므로 수치가 낙관적입니다. 운영 승격에는 test 평가가 필요합니다.")
    return EXIT_OK


def cmd_promote(args: argparse.Namespace, config: AppConfig) -> int:
    from .promotion import PromotionBlocked, promote

    services = _services(config)
    try:
        result = promote(services, args.model, Path(args.report), args.stage, args.reason)
    except PromotionBlocked as exc:
        _print("승격 차단:")
        for reason in exc.reasons:
            _print(f"  - {reason}")
        if args.stage == "production":
            _print("기준을 낮춰 통과시키지 않습니다. 파일럿으로 쓰려면 --stage pilot --reason \"사유\"를 지정하세요.")
        return EXIT_BLOCKED
    _print(f"{result['model_version']}을(를) {result['stage']} 단계로 승격했습니다 (정책 {result['policy_version']}).")
    _print(_json(result["details"]))
    return EXIT_OK


def cmd_retrain(args: argparse.Namespace, config: AppConfig) -> int:
    """train.bat의 본체: 판정으로 다시 학습 → 평가 → 사람이 답하면 운영 모델 교체."""
    from .retrain import RetrainBlocked, retrain

    services = _services(config)
    try:
        retrain(services, name=args.name, decision=args.promote, stage=args.stage, reason=args.reason,
                say=_print, ask=input)
    except RetrainBlocked as exc:
        _print(f"중단: {exc}")
        return EXIT_BLOCKED
    return EXIT_OK


def cmd_rollback(args: argparse.Namespace, config: AppConfig) -> int:
    from .promotion import rollback

    services = _services(config)
    result = rollback(services, args.model, args.reason)
    previous = result["previous"]["model_version"] if result["previous"] else "없음"
    _print(f"운영 모델을 {previous} → {result['model_version']} ({result['stage']}, 정책 {result['policy_version']})로 되돌렸습니다.")
    return EXIT_OK


# ====================================================================== 보조
def cmd_status(args: argparse.Namespace, config: AppConfig) -> int:
    from .storage import open_database
    from .ui.server import ReviewApp

    status = ReviewApp(open_database(config.database_path), config).status()
    status.pop("reason_codes")
    status.pop("filters")
    _print(_json(status))
    return EXIT_OK


def cmd_backup(args: argparse.Namespace, config: AppConfig) -> int:
    from .storage import open_database

    path = open_database(config.database_path).backup(Path(args.output))
    _print(f"백업 완료: {path}\n임베딩 캐시({config.data_dir / 'embeddings'})와 artifacts는 별도로 복사하세요.")
    return EXIT_OK


def cmd_restore(args: argparse.Namespace, config: AppConfig) -> int:
    from .storage import Database

    saved = Database(config.database_path).restore(Path(args.source))
    _print(f"복구 완료. 이전 DB 사본: {saved}")
    return EXIT_OK


def cmd_verify_run(args: argparse.Namespace, config: AppConfig) -> int:
    """저장된 예측을 같은 bundle과 캐시로 다시 계산해 재현되는지 확인한다."""
    from .classifiers.registry import load_registered_bundle
    from .policies.thresholds import decide
    from .scoring import ensure_compatible, score_segments

    services = _services(config)
    rows = services.database.query(
        "SELECT p.segment_id, p.stage1_score, p.threshold, p.final_decision, p.classifier_version, "
        "s.normalized_text, s.context_segment_ids_json FROM predictions p JOIN segments s ON s.segment_id = p.segment_id "
        "WHERE p.run_id = ? ORDER BY s.document_id, s.seq", (args.run,))
    if not rows:
        _print(f"run {args.run}에 저장된 예측이 없습니다.")
        return EXIT_ERROR
    versions = {row["classifier_version"] for row in rows}
    bundle = load_registered_bundle(services.database, versions.pop(), config.base_dir)
    ensure_compatible(services, bundle)
    segments = [{"segment_id": row["segment_id"], "normalized_text": row["normalized_text"],
                 "context_segment_ids": json.loads(row["context_segment_ids_json"])} for row in rows]
    scores, _ = score_segments(services, bundle, segments)
    worst = max(abs(float(score) - row["stage1_score"]) for score, row in zip(scores, rows))
    flipped = sum(decide(float(score), row["threshold"]) != row["final_decision"] for score, row in zip(scores, rows))
    ok = worst < 1e-6 and flipped == 0
    _print(f"run {args.run}: 예측 {len(rows)}건 재계산 · 최대 점수 차이 {worst:.2e} · 판정 불일치 {flipped}건 → {'재현됨' if ok else '불일치'}")
    return EXIT_OK if ok else EXIT_ERROR


def cmd_benchmark(args: argparse.Namespace, config: AppConfig) -> int:
    """합성 문장으로 임베딩 처리 성능을 잰다. 수치는 이 PC에서의 측정값이다."""
    import statistics

    from .classifiers.seed import load_seed_examples
    from .embeddings.e5 import load_e5
    from .runtime import MemoryMonitor, atomic_write_json, enter_offline_mode, total_memory_gib

    if config.runtime.offline:
        enter_offline_mode()
    memory = MemoryMonitor(config.runtime.process_tree_memory_budget_gib)
    started = time.perf_counter()
    encoder, tokenizer = load_e5(config, memory=memory)
    encoder.encode(["warm-up"])
    cold_start = time.perf_counter() - started
    examples, _ = load_seed_examples()
    texts = [f"{examples[i % len(examples)]['text']} (시험 문장 {i})" + (" " + examples[(i * 7) % len(examples)]["text"] if i % 3 == 0 else "")
             for i in range(args.segments)]
    lengths = sorted(tokenizer.count(text) + tokenizer.overhead for text in texts)
    started = time.perf_counter()
    encoder.encode(texts)
    batch_seconds = time.perf_counter() - started
    singles = []
    for text in texts[: min(200, len(texts))]:
        tick = time.perf_counter()
        encoder.encode([text])
        singles.append((time.perf_counter() - tick) * 1000)
    singles.sort()
    report = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "합성 문장 기준의 이 PC 측정값. 대상 운영 PC에서 다시 측정해야 한다.",
        "cpu": platform.processor() or platform.machine(), "logical_cores": os.cpu_count(),
        "platform": platform.platform(), "ram_gib": total_memory_gib(), "threads": config.runtime.cpu_threads,
        "batch_size": config.runtime.batch_size, "segments": len(texts),
        "token_length": {"min": lengths[0], "median": lengths[len(lengths) // 2],
                         "p95": lengths[int(len(lengths) * 0.95)], "max": lengths[-1]},
        "cold_start_seconds": round(cold_start, 3), "batch_seconds": round(batch_seconds, 3),
        "segments_per_second": round(len(texts) / batch_seconds, 1),
        "single_segment_ms": {"p50": round(statistics.median(singles), 2), "p95": round(singles[int(len(singles) * 0.95)], 2)},
        "memory": memory.summary(),
        "target": {"warm_minutes_for_1000_segments": 10, "met": batch_seconds * (1000 / len(texts)) <= 600},
    }
    path = config.artifacts_dir / f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    atomic_write_json(path, report)
    _print(_json(report))
    _print(f"보고서: {path}")
    return EXIT_OK


# ====================================================================== 진입점
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="설정 파일 경로 (기본: ./config/default.yaml)")
    parser = argparse.ArgumentParser(prog="patent_marker", description="오프라인 특허 검토 후보 마킹 시스템",
                                     parents=[common])
    parser.add_argument("--version", action="version", version=f"patent-marker {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler: Any, help_text: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help_text, parents=[common])
        command.set_defaults(handler=handler)
        return command

    doctor = add("doctor", cmd_doctor, "환경·로컬 모델·DB 점검")
    doctor.add_argument("--offline", action="store_true", help="오프라인 모드로 점검 (네트워크 접근 시 실패)")

    ingest = add("ingest", cmd_ingest, "문서를 파싱해 DB에 넣기 (검토·라벨링용)")
    ingest.add_argument("--input", required=True, help="파일 또는 디렉터리")
    ingest.add_argument("--encoding", help="TXT/MD 인코딩 (기본 UTF-8)")

    analyze = add("analyze", cmd_analyze, "문서 분석 후 결과물(HTML, JSONL, 마킹 사본) 생성")
    analyze.add_argument("--input", required=True, help="파일 또는 디렉터리")
    analyze.add_argument("--output", required=True, help="결과 디렉터리 (예: ./outputs/run-001)")
    analyze.add_argument("--run-id", help="run 이름 (기본: 출력 디렉터리 이름)")
    analyze.add_argument("--encoding", help="TXT/MD 인코딩 (기본 UTF-8)")
    analyze.add_argument("--format", help="html,jsonl,annotated 중 선택 (기본: 설정값)")

    mark = add("mark", cmd_mark, "파일·폴더를 바로 분석 (mark.bat: 열기 창에서 고르거나 끌어다 놓기)")
    mark.add_argument("paths", nargs="*", help="분석할 파일 또는 폴더 (여러 개 가능)")
    mark.add_argument("--pick", action="store_true", help="경로를 주지 않았으면 파일 열기 창에서 고르기")
    mark.add_argument("--open", action="store_true", help="끝나면 결과 폴더 열기 (Windows)")
    mark.add_argument("--encoding", help="TXT/MD 인코딩 (기본 UTF-8)")

    laya = add("laya-compare", cmd_laya_compare, "실험: 저장된 run의 1차 후보를 Laya로 다시 판단해 비교 자료 만들기")
    laya.add_argument("--run", required=True)
    laya.add_argument("--output", help="비교 자료 폴더 (기본: outputs/<run>/laya)")
    laya.add_argument("--agree-at", type=float, default=0.5,
                      help="Laya 점수가 이 값 이상이면 동의로 본다 (기본 0.5, 검증되지 않은 값)")

    export = add("export", cmd_export, "저장된 run의 결과물 다시 만들기")
    export.add_argument("--run", required=True)
    export.add_argument("--output", help="결과 디렉터리 (기본: outputs/<run>)")
    export.add_argument("--format", help="html,jsonl,annotated 중 선택")

    review = add("review", cmd_review, "로컬 검토 화면 실행")
    review.add_argument("--host", default=None, help="127.0.0.1만 허용")
    review.add_argument("--port", type=int, default=None)

    snapshot = add("snapshot", cmd_snapshot, "확정 라벨 snapshot과 train/validation/test 분할 고정")
    snapshot.add_argument("--name", required=True, help="예: labels-v1")
    snapshot.add_argument("--output", help="JSONL 경로 (기본: data/snapshots/<name>.jsonl)")

    train = add("train", cmd_train, "snapshot으로 분류기 학습 (운영 모델은 바뀌지 않음)")
    train.add_argument("--snapshot", required=True)

    experiment = add("experiment", cmd_experiment, "특징 구성·C를 그룹 교차검증으로 비교")
    experiment.add_argument("--snapshot", required=True)
    experiment.add_argument("--modes", default="separate,target_only,composed")
    experiment.add_argument("--c-values", help="예: 0.3,1,3")

    evaluate = add("evaluate", cmd_evaluate, "모델 평가 보고서 생성")
    evaluate.add_argument("--model", required=True, help="예: classifier-0001")
    evaluate.add_argument("--split", required=True, help="예: validation-v1, test-v1")
    evaluate.add_argument("--output", help="보고서 JSON 경로")

    promote = add("promote", cmd_promote, "평가 기준을 통과한 모델을 운영 모델로 승격")
    promote.add_argument("--model", required=True)
    promote.add_argument("--report", required=True, help="evaluate가 만든 보고서 JSON")
    promote.add_argument("--stage", choices=("production", "pilot"), default="production")
    promote.add_argument("--reason", help="파일럿 승격 사유")

    retrain = add("retrain", cmd_retrain, "판정으로 다시 학습 → 평가 → 확인 뒤 운영 모델 교체 (train.bat이 쓰는 명령)")
    retrain.add_argument("--name", help="snapshot 이름 (기본: labels-날짜-시각)")
    retrain.add_argument("--promote", choices=("ask", "yes", "no"), default="ask",
                         help="평가 뒤 운영 모델로 바꿀지. ask는 화면에서 묻는다 (입력이 닫혀 있으면 no)")
    retrain.add_argument("--stage", choices=("auto", "pilot", "production"), default="auto",
                         help="auto: 정식 승격 조건을 채우면 production, 아니면 pilot")
    retrain.add_argument("--reason", help="pilot 승격 사유")

    rollback = add("rollback", cmd_rollback, "이전 운영 모델로 되돌리기")
    rollback.add_argument("--model", required=True, help="예: classifier-0000")
    rollback.add_argument("--reason")

    add("status", cmd_status, "운영 모델·라벨 현황")

    backup = add("backup", cmd_backup, "DB 백업")
    backup.add_argument("--output", required=True)
    restore = add("restore", cmd_restore, "DB 복구")
    restore.add_argument("--from", dest="source", required=True)

    verify = add("verify-run", cmd_verify_run, "과거 run의 판정 재현 확인")
    verify.add_argument("--run", required=True)

    benchmark = add("benchmark", cmd_benchmark, "임베딩 처리 성능 측정")
    benchmark.add_argument("--segments", type=int, default=1000)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        _print(f"설정 오류: {exc}")
        return EXIT_ERROR
    try:
        return args.handler(args, config)
    except KeyboardInterrupt:
        _print("\n중단되었습니다. 같은 명령을 다시 실행하면 이어서 진행합니다.")
        return 130
    except Exception as exc:
        # 본문 내용이 섞이지 않도록 오류 종류와 메시지만 출력한다.
        _print(f"오류: {type(exc).__name__}: {exc}")
        if os.environ.get("PATENT_MARKER_DEBUG"):
            raise
        return EXIT_ERROR
    finally:
        # 명령이 끝나면 DB 연결을 닫는다. 열어 둔 채로 두면 Windows에서 DB 파일을 교체(restore)할 수 없다.
        from .storage import close_all

        close_all()


if __name__ == "__main__":
    raise SystemExit(main())
