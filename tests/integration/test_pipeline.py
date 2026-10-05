"""라벨 수집 → 학습 → 독립 평가 → 승격 → 롤백 전체 흐름과 안전장치 (스펙 10·12·16절, 17.1).

가짜 인코더를 주입하므로 모델 파일 없이 실행된다. 실제 E5 경로는 test_real_model.py에서 확인한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import patent_marker.analysis as analysis_module
from helpers import FIXTURES, label_everything, make_services, write_corpus
from patent_marker.analysis import RunError, run_analysis
from patent_marker.classifiers.registry import SEED_MODEL_VERSION, get_active, get_model, register_model, set_active
from patent_marker.classifiers.seed import load_seed_examples
from patent_marker.classifiers.train import TrainingBlocked
from patent_marker.evaluation.reports import EvaluationError, evaluate_model
from patent_marker.export.runner import export_run
from patent_marker.feedback.events import record_feedback
from patent_marker.feedback.snapshot import create_snapshot, load_snapshot
from patent_marker.promotion import PromotionBlocked, promote, rollback
from patent_marker.runtime import sha256_file
from patent_marker.scoring import load_registered_bundle, score_segments
from patent_marker.training import compare_feature_modes, train_from_snapshot

RELAXED = {"promotion.min_positive_segments": 20, "promotion.min_document_families": 4,
           "evaluation.bootstrap_samples": 200}


def _prepare(tmp_path: Path, families: int = 24, **overrides):
    services = make_services(tmp_path, **overrides)
    inbox = tmp_path / "inbox"
    expected = write_corpus(inbox, families=families)
    summary = run_analysis(services, inbox, "run-001")
    labeled = label_everything(services, expected)
    return services, inbox, expected, summary, labeled


def test_seed_data_is_balanced_and_unique():
    examples, digest = load_seed_examples()
    labels = [example["label"] for example in examples]
    assert labels.count("YES") >= 100 and labels.count("NO") >= 100 and len(digest) == 64
    assert len({example["text"] for example in examples}) == len(examples)


def test_full_flow_from_labels_to_production_and_rollback(tmp_path):
    services, inbox, expected, summary, labeled = _prepare(tmp_path, **RELAXED)
    database = services.database
    # 1) 첫 분석은 합성 seed 분류기(SEED · 미검증)로 수행된다
    assert summary["status"] == "COMPLETED" and summary["model_version"] == SEED_MODEL_VERSION
    assert summary["stage"] == "SEED" and summary["predicted"] == summary["segments"] == 288 == labeled
    seed_policy = database.query_one("SELECT status FROM policies WHERE model_version = ?", (SEED_MODEL_VERSION,))
    assert seed_policy["status"] == "SEED_UNVALIDATED"

    # 2) snapshot: 문서 계열 단위 분할, 누수 없음
    snapshot = create_snapshot(database, services.config, "labels-v1")
    rows = load_snapshot(database, snapshot["path"])["rows"]
    by_partition: dict[str, set[str]] = {}
    for row in rows:
        by_partition.setdefault(row["partition"], set()).add(row["document_family_id"])
    assert set(by_partition) == {"train", "validation", "test"}
    assert not (by_partition["train"] & by_partition["test"]) and not (by_partition["train"] & by_partition["validation"])
    assert not (by_partition["validation"] & by_partition["test"])
    assert snapshot["stats"]["families"] == 24 and snapshot["stats"]["yes"] == 120

    # 3) 학습: 운영 모델은 바뀌지 않는다
    trained = train_from_snapshot(services, snapshot["path"])
    assert trained["model_version"] == "classifier-0001" and trained["policy_status"] == "VALIDATED"
    assert trained["selection"]["basis"] == "validation_partition" and trained["selection"]["recall"] >= 0.95
    assert trained["converged"] and trained["experimental"]  # 라벨 200개 미만 → 실험 모델 표시
    assert get_active(database)["model_version"] == SEED_MODEL_VERSION
    bundle = load_registered_bundle(database, "classifier-0001", services.config.base_dir)
    assert set(bundle.training["train_families"]) == by_partition["train"]
    assert bundle.training["seed"]["sample_weight"] == 0.3

    # 4) 독립 test 평가
    report = evaluate_model(services, "classifier-0001", "test-v1")
    assert report["independent"] and report["leakage"]["ok"] and not report["threshold_selected_on_this_partition"]
    at = report["segment_level"]["at_threshold"]
    assert at["tp"] + at["fn"] == report["counts"]["positive_segments"] and at["recall"] >= 0.95
    assert at["recall_ci95"]["method"].startswith("document-family bootstrap")
    assert report["paragraph_level"]["at_threshold"]["recall"] == at["recall"]  # 문단당 segment 하나
    completeness = report["label_completeness"]
    assert completeness["fully_labeled_documents"] == completeness["documents"]
    assert report["end_to_end"]["fn_unparsed"] == 0 and Path(report["report_path"]).with_suffix(".md").is_file()
    assert set(report["slices"]) == {"format", "language", "length", "table", "label_source"}

    # 5) 승격 → 새 run은 새 모델을 쓴다
    promoted = promote(services, "classifier-0001", report["report_path"], "production")
    assert promoted["stage"] == "PRODUCTION" and get_active(database)["model_version"] == "classifier-0001"
    assert get_model(database, SEED_MODEL_VERSION)["retired_at"] is not None
    second = run_analysis(services, inbox, "run-002")
    assert second["model_version"] == "classifier-0001" and second["stage"] == "PRODUCTION"
    # 과거 run의 예측은 덮어쓰지 않는다
    versions = {row["run_id"]: row["classifier_version"] for row in database.query(
        "SELECT DISTINCT run_id, classifier_version FROM predictions")}
    assert versions == {"run-001": SEED_MODEL_VERSION, "run-002": "classifier-0001"}

    # 6) 롤백: 이전 모델 + 정책 bundle로 복귀
    back = rollback(services, SEED_MODEL_VERSION, reason="시험")
    assert back["stage"] == "SEED" and get_active(database) == {
        "model_version": SEED_MODEL_VERSION, "policy_version": back["policy_version"], "stage": "SEED"}
    assert rollback(services, "classifier-0001")["stage"] == "PRODUCTION"
    actions = [row["action"] for row in database.query("SELECT action FROM promotion_events ORDER BY id")]
    assert actions == ["SEED_INIT", "PROMOTE", "ROLLBACK", "ROLLBACK"]


def test_past_predictions_can_be_reproduced_from_bundle_and_cache(tmp_path):
    services, *_ = _prepare(tmp_path, families=6)
    rows = services.database.query(
        "SELECT p.segment_id, p.stage1_score, p.classifier_version, s.normalized_text, s.context_segment_ids_json "
        "FROM predictions p JOIN segments s USING (segment_id) WHERE p.run_id = 'run-001' ORDER BY s.document_id, s.seq")
    bundle = load_registered_bundle(services.database, rows[0]["classifier_version"], services.config.base_dir)
    segments = [{"segment_id": r["segment_id"], "normalized_text": r["normalized_text"],
                 "context_segment_ids": json.loads(r["context_segment_ids_json"])} for r in rows]
    encoded_before = services.encoder.encoded
    scores, _ = score_segments(services, bundle, segments)
    assert services.encoder.encoded == encoded_before  # 캐시된 임베딩만으로 재현
    assert np.allclose(scores, [r["stage1_score"] for r in rows], atol=1e-9)


def test_production_promotion_is_blocked_until_criteria_are_met(tmp_path):
    services, *_ = _prepare(tmp_path, **{"evaluation.bootstrap_samples": 100})  # 승격 기준은 스펙 기본값
    snapshot = create_snapshot(services.database, services.config, "labels-v1")
    train_from_snapshot(services, snapshot["path"])

    test_report = evaluate_model(services, "classifier-0001", "test-v1")
    with pytest.raises(PromotionBlocked) as blocked:
        promote(services, "classifier-0001", test_report["report_path"], "production")
    text = " ".join(blocked.value.reasons)
    assert "양성 구간" in text and "문서 계열" in text  # 표본 수 미달 → 파일럿 상태
    assert get_active(services.database)["model_version"] == SEED_MODEL_VERSION  # 운영 모델 불변

    validation_report = evaluate_model(services, "classifier-0001", "validation-v1")
    assert validation_report["threshold_selected_on_this_partition"]
    with pytest.raises(PromotionBlocked, match="독립 test"):
        promote(services, "classifier-0001", validation_report["report_path"], "production")

    train_report = evaluate_model(services, "classifier-0001", "train-v1")
    assert not train_report["leakage"]["ok"] and train_report["leakage"]["overlapping_families"]
    with pytest.raises(PromotionBlocked, match="누수"):
        promote(services, "classifier-0001", train_report["report_path"], "pilot", reason="누수 시험")

    with pytest.raises(PromotionBlocked, match="사유"):
        promote(services, "classifier-0001", test_report["report_path"], "pilot")

    tampered = Path(test_report["report_path"])
    original = tampered.read_text(encoding="utf-8")
    tampered.write_text(original.replace('"independent": true', '"independent":  true'), encoding="utf-8")
    with pytest.raises(PromotionBlocked, match="내용이 바뀐"):
        promote(services, "classifier-0001", tampered, "pilot", reason="변조 시험")
    tampered.write_text(original, encoding="utf-8")

    piloted = promote(services, "classifier-0001", test_report["report_path"], "pilot", reason="표본 부족, 시범 사용")
    assert piloted["stage"] == "PILOT"
    blocked_events = services.database.query_one("SELECT COUNT(*) FROM promotion_events WHERE action = 'BLOCKED'")[0]
    assert blocked_events == 5
    with pytest.raises(EvaluationError):
        evaluate_model(services, "classifier-0001", "holdout")


def test_small_validation_falls_back_to_group_cross_validation(tmp_path):
    services, *_ = _prepare(tmp_path, families=6)
    snapshot = create_snapshot(services.database, services.config, "labels-v1")
    trained = train_from_snapshot(services, snapshot["path"])
    assert trained["policy_status"] == "CV_ESTIMATE" and trained["selection"]["basis"] == "group_cross_validation"
    assert trained["threshold"] is not None
    report = evaluate_model(services, trained["model_version"], "test-v1")
    with pytest.raises(PromotionBlocked, match="validation partition에서 검증되지"):
        promote(services, trained["model_version"], report["report_path"], "production")
    comparison = compare_feature_modes(services, snapshot["path"], ["separate", "target_only", "composed"])
    assert [row["mode"] for row in comparison["results"]] == ["separate", "target_only", "composed"]
    assert all(row["average_precision"] is not None for row in comparison["results"])


def test_training_is_blocked_when_only_one_class_is_labelled(tmp_path):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    expected = write_corpus(inbox, families=4)
    run_analysis(services, inbox, "run-001")
    label_everything(services, {text: label for text, label in expected.items() if label == "NO"})
    snapshot = create_snapshot(services.database, services.config, "labels-v1")
    with pytest.raises(TrainingBlocked, match="한 클래스"):
        train_from_snapshot(services, snapshot["path"])
    assert services.database.query_one("SELECT COUNT(*) FROM model_registry")[0] == 1  # seed만 있음


def test_untrained_mode_without_seed_has_null_scores(tmp_path):
    services = make_services(tmp_path, **{"seed.enabled": False})
    summary = run_analysis(services, FIXTURES, "run-001")
    assert summary["status"] == "UNTRAINED" and summary["model_version"] is None and summary["predicted"] == 0
    assert services.database.query_one("SELECT COUNT(*) FROM predictions")[0] == 0
    result = export_run(services.database, services.config, "run-001", tmp_path / "out", ["html", "jsonl"])
    rows = [json.loads(line) for line in Path(result["files"]["jsonl"]["path"]).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 64
    assert all(row["stage1_score"] is None and row["final_decision"] is None for row in rows)  # 0점·NO로 바꾸지 않는다
    assert any(row["rule_hints"] for row in rows)
    html = Path(result["files"]["html"]["path"]).read_text(encoding="utf-8")
    assert "UNTRAINED" in html and "미판정" in html


def test_interrupted_run_resumes_without_duplicates_and_keeps_its_model(tmp_path, monkeypatch):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    write_corpus(inbox, families=5)
    real_ingest = analysis_module.ingest_file
    calls = {"n": 0}

    def flaky(services_, path, encoding=None):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt
        return real_ingest(services_, path, encoding)

    monkeypatch.setattr(analysis_module, "ingest_file", flaky)
    with pytest.raises(KeyboardInterrupt):
        run_analysis(services, inbox, "run-001")
    database = services.database
    assert database.query_one("SELECT status FROM runs WHERE run_id = 'run-001'")[0] == "INTERRUPTED"
    assert database.query_one("SELECT COUNT(*) FROM predictions")[0] == 24  # 문서 2개분

    # 중단된 사이에 다른 모델이 운영 모델이 되어도, 이 run은 시작 시점의 모델을 끝까지 쓴다
    seed_bundle = load_registered_bundle(database, SEED_MODEL_VERSION, services.config.base_dir)
    seed_bundle.model_version = "classifier-0001"
    seed_bundle.coef = [-value for value in seed_bundle.coef]
    from patent_marker.classifiers.bundle import save_bundle

    path = services.config.artifacts_dir / "models" / "classifier-0001.json"
    register_model(database, seed_bundle, path, save_bundle(seed_bundle, path), services.config.base_dir, None,
                   get_model(database, SEED_MODEL_VERSION)["policy_version"])
    set_active(database, "classifier-0001", get_model(database, SEED_MODEL_VERSION)["policy_version"], "PILOT", "PROMOTE")

    monkeypatch.setattr(analysis_module, "ingest_file", real_ingest)
    summary = run_analysis(services, inbox, "run-001")
    assert summary["status"] == "COMPLETED" and summary["model_version"] == SEED_MODEL_VERSION
    assert database.query_one("SELECT COUNT(*), COUNT(DISTINCT segment_id) FROM predictions")[:] == (60, 60)
    assert {row[0] for row in database.query("SELECT DISTINCT classifier_version FROM predictions")} == {SEED_MODEL_VERSION}
    assert database.query_one("SELECT COUNT(*) FROM documents")[0] == 5
    with pytest.raises(RunError, match="이미 완료된"):
        run_analysis(services, inbox, "run-001")
    assert run_analysis(services, inbox, "run-002")["model_version"] == "classifier-0001"


def test_originals_are_never_modified_by_analysis_or_export(tmp_path):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("sample_report.pptx", "sample_report.pdf", "sample_report.docx"):
        (inbox / name).write_bytes((FIXTURES / name).read_bytes())
    before = {path.name: sha256_file(path) for path in inbox.iterdir()}
    run_analysis(services, inbox, "run-001")
    for row in services.database.query("SELECT segment_id FROM segments LIMIT 40"):
        record_feedback(services.database, target_type="segment", target_id=row["segment_id"], label="YES",
                        reviewer_id="reviewer-01", guideline_version="1.0")
    result = export_run(services.database, services.config, "run-001", tmp_path / "outputs" / "run-001")
    assert {path.name: sha256_file(path) for path in inbox.iterdir()} == before  # 원본 해시 불변, 새 파일 없음
    outputs = sorted(path.name for path in (tmp_path / "outputs" / "run-001" / "marked").iterdir())
    assert outputs == ["sample_report.marked.pdf", "sample_report.marked.pptx"]
    statuses = {entry["file_name"]: entry["status"] for entry in result["annotated"]}
    assert statuses == {"sample_report.docx": "not_implemented", "sample_report.pdf": "ok", "sample_report.pptx": "ok"}
    from patent_marker.export.data import ExportError

    with pytest.raises(ExportError, match="원본 문서의 디렉터리"):
        export_run(services.database, services.config, "run-001", inbox)


def test_seed_model_is_rebuilt_when_the_encoder_changes(tmp_path):
    from helpers import FakeEncoder, FakeTokenizer, make_config
    from patent_marker.scoring import IncompatibleBundle
    from patent_marker.services import build_services

    class OtherEncoder(FakeEncoder):
        config_hash = "f" * 64

    inbox = tmp_path / "inbox"
    expected = write_corpus(inbox, families=4)
    first = make_services(tmp_path)
    assert run_analysis(first, inbox, "run-001")["model_version"] == SEED_MODEL_VERSION

    # 인코더를 바꾸면 임베딩 캐시가 분리되고, 호환되지 않는 seed는 새 버전으로 다시 만들어진다
    second = build_services(make_config(tmp_path), tokenizer=FakeTokenizer(), encoder=OtherEncoder())
    summary = run_analysis(second, inbox, "run-002")
    assert summary["model_version"] == "classifier-0001" and summary["stage"] == "SEED"
    assert get_model(second.database, "classifier-0001")["kind"] == "seed"
    assert run_analysis(second, inbox, "run-003")["model_version"] == "classifier-0001"  # 다시 만들지 않는다

    # 학습된 모델이 운영 중이면 자동으로 바꾸지 않고 호환성 오류를 낸다
    label_everything(second, expected)
    snapshot = create_snapshot(second.database, second.config, "labels-v1")
    trained = train_from_snapshot(second, snapshot["path"])
    set_active(second.database, trained["model_version"], trained["policy_version"], "PILOT", "PROMOTE")
    with pytest.raises(IncompatibleBundle, match="인코더"):
        run_analysis(first, inbox, "run-004")
