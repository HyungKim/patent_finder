"""Laya 비교 실험(laya-compare) 시험 (스펙 8절).

가짜 2단계 모델과 가짜 SDK를 쓰므로 Laya SDK와 PyTorch 없이 돈다.
실제 Laya 모델을 불러오는 시험은 requires_laya 표시가 있고, 별도 환경(.venv-laya)에서만 실행된다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import types
from pathlib import Path

import pytest

from helpers import FIXTURES, LAYA_MODEL_DIR, make_services
from patent_marker import runtime
from patent_marker.analysis import run_analysis
from patent_marker.export.data import ExportError
from patent_marker.export.runner import export_run
from patent_marker.feedback.events import record_feedback
from patent_marker.runtime import sha256_file
from patent_marker.second_stage.adapter import Assessment
from patent_marker.second_stage.compare import run_comparison
from patent_marker.second_stage.laya import (QUESTION, QUESTION_ID, LayaSecondStage, LayaUnavailable, compose_state)

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
DANGER = ("<script>alert(1)</script> 센서 측정값이 임계값 30을 넘으면 <b>밸브</b> 출력을 20% 낮추고 & "
          "기준 이하에서 이전 값으로 복귀한다.")
SDK_CHECKED = ("model.safetensors", "tokenizer/tokenizer.json", "rl_agent_config.json", "encoder/config.json")


class FakeSecondStage:
    """문장마다 정해 둔 점수를 돌려주는 가짜 2단계 모델. 점수가 None이면 판단에 실패한 것으로 한다."""

    model_version = "fake-laya@test"
    question_version = "fake-q1"

    def __init__(self, scores: dict[str, float | None] | None = None, default: float | None = 0.9,
                 agree_at: float = 0.5) -> None:
        self.scores, self.default, self.agree_at = scores or {}, default, agree_at
        self.seen: list[tuple[str, str]] = []

    def assess_batch(self, items):
        self.seen.extend(items)
        assessments = []
        for target, _context in items:
            score = self.scores.get(target, self.default)
            if score is None:
                assessments.append(Assessment(status="error", error_code="RuntimeError"))
            else:
                assessments.append(Assessment(
                    status="ok", raw_scores={"means": score, "scale": "p_yes", "truncated": False},
                    decision="AGREE" if score >= self.agree_at else "DISAGREE", latency_ms=1.0))
        return assessments


def _tree(directory: Path, skip: str | None = None) -> dict[str, str]:
    """폴더 안 모든 파일의 해시. skip은 건너뛸 하위 폴더 이름."""
    return {path.relative_to(directory).as_posix(): sha256_file(path) for path in sorted(directory.rglob("*"))
            if path.is_file() and skip not in path.relative_to(directory).parts}


def _tables(database) -> dict[str, list[tuple]]:
    """2단계 결과 표를 뺀 모든 표의 내용."""
    names = [row[0] for row in database.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {name: [tuple(row) for row in database.query(f"SELECT * FROM {name} ORDER BY 1")]
            for name in names if name != "second_stage_results"}


def _candidates(database, file_name: str | None = None) -> list:
    return database.query(
        "SELECT s.segment_id, s.normalized_text, p.prediction_id, par.kind, par.section_title, d.file_name "
        "FROM predictions p JOIN segments s USING (segment_id) JOIN documents d ON d.document_id = s.document_id "
        "JOIN paragraphs par ON par.paragraph_id = s.paragraph_id "
        "WHERE p.run_id = 'run-001' AND p.final_decision = 'CANDIDATE' AND (? IS NULL OR d.file_name = ?) "
        "ORDER BY d.file_name, s.seq", (file_name, file_name))


def _highlighted(pptx_path: Path) -> str:
    """PPTX에서 형광 표시된 글자를 공백 없이 이어 붙인 것."""
    from pptx import Presentation

    parts = []
    for slide in Presentation(str(pptx_path)).slides:
        for run in slide._element.iter(A + "r"):
            if next(run.iter(A + "highlight"), None) is not None:
                parts.append("".join(node.text or "" for node in run.iter(A + "t")))
    return re.sub(r"\s+", "", "".join(parts))


def _annotation_count(pdf_path: Path) -> int:
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        return sum(len(page.annots) for page in pdf.pages)


@pytest.fixture
def analyzed(tmp_path):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for path in FIXTURES.iterdir():
        (inbox / path.name).write_bytes(path.read_bytes())
    (inbox / "danger.txt").write_bytes((DANGER + "\n").encode("utf-8"))
    run_analysis(services, inbox, "run-001")
    output = tmp_path / "outputs" / "run-001"
    export_run(services.database, services.config, "run-001", output, ["html", "jsonl", "annotated"])
    return services, inbox, output


# ---------------------------------------------------------------- 1차 결과는 그대로
def test_comparison_adds_results_and_leaves_first_stage_outputs_untouched(analyzed):
    services, inbox, output = analyzed
    database = services.database
    candidates = _candidates(database)
    texts = [row["normalized_text"] for row in candidates]
    assert len(candidates) == 41 and DANGER in texts
    low = set(texts[::3])  # 세 개 중 하나꼴로 Laya가 '아니다'라고 본다
    adapter = FakeSecondStage({text: 0.2 for text in low})
    files, inputs, tables = _tree(output), _tree(inbox), _tables(database)

    summary = run_comparison(database, services.config, "run-001", adapter, output / "laya")

    # 기존 결과물, 원본 문서, 1차 판정을 포함한 다른 모든 표는 그대로다
    assert _tree(output, skip="laya") == files and _tree(inbox) == inputs and _tables(database) == tables
    again = export_run(database, services.config, "run-001", output.parent / "again", ["jsonl", "annotated"])
    assert (output.parent / "again" / "results.jsonl").read_bytes() == (output / "results.jsonl").read_bytes()
    assert {entry["file_name"]: entry.get("marked") for entry in again["annotated"]
            if entry["status"] == "ok"} == {"sample_report.pdf": 10, "sample_report.pptx": 15}

    # 2단계 모델에는 1차 후보만, 대상 문장과 그 문단의 제목을 넘긴다
    assert sorted(adapter.seen) == sorted((row["normalized_text"], row["section_title"] or "") for row in candidates)
    disagreed = sum(text in low for text in texts)
    assert summary["candidates"] == 41 and summary["disagree"] == disagreed and summary["agree"] == 41 - disagreed
    assert summary["not_assessed"] == 0 and summary["agree_at"] == 0.5 and summary["model_version"] == "fake-laya@test"

    # 같은 1차 prediction ID에 결과를 덧붙여 저장한다 (표본 선택 방식과 포함 확률, 기준값 포함)
    stored = database.query("SELECT * FROM second_stage_results")
    assert {row["prediction_id"] for row in stored} == {row["prediction_id"] for row in candidates} and len(stored) == 41
    by_prediction = {row["prediction_id"]: row for row in stored}
    for row in candidates:
        saved = by_prediction[row["prediction_id"]]
        expected = 0.2 if row["normalized_text"] in low else 0.9
        assert json.loads(saved["raw_scores_json"]) == {"means": expected, "scale": "p_yes", "truncated": False}
        assert saved["decision"] == ("DISAGREE" if expected < 0.5 else "AGREE") and saved["status"] == "ok"
        assert (saved["model_revision"], saved["question_version"]) == ("fake-laya@test", "fake-q1")
        assert saved["calibration_version"] == "none:agree_at=0.5" and saved["error_code"] is None
        assert saved["sampling_reason"] == "stage1_candidates_all" and saved["inclusion_probability"] == 1.0

    # 비교 자료
    rows = [json.loads(line) for line in (output / "laya" / "laya_results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["segment_id"] for row in rows] == [row["segment_id"] for row in candidates]
    assert all(row["laya_status"] == "ok" and row["verdict"] in ("동의", "이견") and row["agree_at"] == 0.5 for row in rows)
    assert sum(row["laya_decision"] == "DISAGREE" for row in rows) == disagreed
    saved_summary = json.loads((output / "laya" / "laya_summary.json").read_text(encoding="utf-8"))
    assert {key: saved_summary[key] for key in ("candidates", "agree", "disagree", "not_assessed")} == {
        "candidates": 41, "agree": 41 - disagreed, "disagree": disagreed, "not_assessed": 0}

    # 보고서는 문서 내용을 그대로 실행 가능한 HTML로 넣지 않고, 밖에서 아무것도 불러오지 않는다
    report = (output / "laya" / "laya_report.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in report and "<script" not in report
    assert "&lt;b&gt;밸브&lt;/b&gt;" in report and "20% 낮추고 &amp; 기준" in report
    assert not re.search(r"https?://", report) and not re.search(r"<(link|img|iframe)\b", report) and "src=" not in report
    assert "default-src 'none'" in report and "검증되지 않은 실험 결과" in report

    # 다시 돌리면 같은 행을 새 결과로 바꾼다 (기준값이 달라지면 판단과 기록이 함께 바뀐다)
    run_comparison(database, services.config, "run-001", FakeSecondStage({text: 0.2 for text in low}, agree_at=0.1),
                   output / "laya")
    stored = database.query("SELECT decision, calibration_version FROM second_stage_results")
    assert len(stored) == 41 and {tuple(row) for row in stored} == {("AGREE", "none:agree_at=0.1")}


# ---------------------------------------------------------------- 비교용 사본
def test_agree_only_copies_drop_what_laya_rejected_but_keep_failures_and_human_yes(analyzed):
    services, inbox, output = analyzed
    database = services.database
    by_text = {row["normalized_text"]: row for row in _candidates(database, "sample_report.pptx")}
    assert len(by_text) == 15
    rejected, human_yes, failed, untouched = ("추진 일정 및 예산", "향후 계획", "다음 분기에 센서 융합 성능 개선을 추진한다.",
                                               "예산 집행률 72%, 잔여 예산은 평가 장비 임차에 사용")
    assert {rejected, human_yes, failed, untouched} <= set(by_text)
    record_feedback(database, target_type="segment", target_id=by_text[human_yes]["segment_id"], label="YES",
                    reviewer_id="reviewer-01", guideline_version="1.0")
    adapter = FakeSecondStage({rejected: 0.1, human_yes: 0.1, failed: None})

    summary = run_comparison(database, services.config, "run-001", adapter, output / "laya")

    assert (summary["agree"], summary["disagree"], summary["not_assessed"]) == (38, 2, 1)
    copies = {entry["file_name"]: entry for entry in summary["copies"]}
    # Laya가 '아니다'라고 한 둘 중 사람이 YES로 판정한 것은 남는다. 판단하지 못한 후보도 남는다.
    assert copies["sample_report.pptx"]["status"] == "ok" and copies["sample_report.pptx"]["marked"] == 14
    assert copies["sample_report.pdf"]["status"] == "ok" and copies["sample_report.pdf"]["marked"] == 10
    laya_dir = output / "laya" / "marked_laya_agree"
    assert sorted(path.name for path in laya_dir.iterdir()) == ["sample_report.marked.pdf", "sample_report.marked.pptx"]

    squeeze = lambda text: re.sub(r"\s+", "", text)  # noqa: E731
    main_marks = _highlighted(output / "marked" / "sample_report.marked.pptx")
    laya_marks = _highlighted(laya_dir / "sample_report.marked.pptx")
    assert all(squeeze(text) in main_marks for text in (rejected, human_yes, failed, untouched))
    assert squeeze(rejected) not in laya_marks
    assert all(squeeze(text) in laya_marks for text in (human_yes, failed, untouched))
    assert _annotation_count(laya_dir / "sample_report.marked.pdf") == _annotation_count(
        output / "marked" / "sample_report.marked.pdf") == 10
    # 원본에는 표시가 없다
    assert _highlighted(inbox / "sample_report.pptx") == "" and _annotation_count(inbox / "sample_report.pdf") == 0

    report = (output / "laya" / "laya_report.html").read_text(encoding="utf-8")
    assert "판단 못 함 1개" in report and report.count("<td>이견</td>") == 2 and report.count("<td>판단 못 함</td>") == 1
    error = database.query_one("SELECT * FROM second_stage_results WHERE prediction_id = ?",
                               (by_text[failed]["prediction_id"],))
    assert (error["status"], error["decision"], error["error_code"], error["raw_scores_json"]) == (
        "error", None, "RuntimeError", "{}")


def test_when_every_assessment_fails_no_candidate_loses_its_mark(analyzed):
    services, _inbox, output = analyzed
    summary = run_comparison(services.database, services.config, "run-001", FakeSecondStage(default=None),
                             output / "laya")
    assert (summary["agree"], summary["disagree"], summary["not_assessed"]) == (0, 0, 41)
    assert {entry["file_name"]: entry["marked"] for entry in summary["copies"] if entry["status"] == "ok"} == {
        "sample_report.pdf": 10, "sample_report.pptx": 15}
    assert _highlighted(output / "laya" / "marked_laya_agree" / "sample_report.marked.pptx") == _highlighted(
        output / "marked" / "sample_report.marked.pptx")


def test_comparison_needs_first_stage_predictions_and_one_assessment_per_candidate(analyzed, tmp_path):
    services, _inbox, output = analyzed
    with pytest.raises(ExportError, match="run을 찾을 수 없습니다"):
        run_comparison(services.database, services.config, "no-such-run", FakeSecondStage(), output / "laya")

    class Short(FakeSecondStage):
        def assess_batch(self, items):
            return super().assess_batch(items)[:-1]

    with pytest.raises(ExportError, match="후보 수"):
        run_comparison(services.database, services.config, "run-001", Short(), output / "laya")
    assert services.database.query_one("SELECT COUNT(*) FROM second_stage_results")[0] == 0
    assert not (output / "laya").exists()

    untrained = make_services(tmp_path / "untrained", **{"seed.enabled": False})
    inbox = tmp_path / "untrained" / "inbox"
    inbox.mkdir()
    (inbox / "note.txt").write_text("측정값이 임계값을 넘으면 출력을 낮춘다.\n", encoding="utf-8")
    run_analysis(untrained, inbox, "run-001")
    with pytest.raises(ExportError, match="1차 판정이 없습니다"):
        run_comparison(untrained.database, untrained.config, "run-001", FakeSecondStage(), tmp_path / "untrained" / "laya")


# ---------------------------------------------------------------- Laya 어댑터 (가짜 SDK)
def _fake_model_dir(root: Path) -> tuple[Path, dict]:
    """manifest와 해시가 맞는 작은 가짜 모델 폴더."""
    model_dir = root / "models" / "laya-multilingual"
    files = {
        "config.json": b"{}", "rl_agent_config.json": b'{"agent": 1}', "encoder/config.json": b'{"encoder": 2}',
        "model.safetensors": bytes(range(64)), "tokenizer/tokenizer.json": b'{"tokenizer": 3}',
        "tokenizer/tokenizer_config.json": b'{"extra_special_tokens": []}', "MODEL_CARD.md": b"card",
    }
    for name, data in files.items():
        (model_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / name).write_bytes(data)
    manifest = {"schema_version": 1, "model_id": "example/laya-test", "revision": "0123456789abcdef0123456789abcdef01234567",
                "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                          for name, data in files.items()}}
    (model_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return model_dir, manifest


class _FakeAgent:
    def __init__(self, score_of) -> None:
        self.score_of, self.calls = score_of, []

    def predict_batch(self, states, questions, batch_size):
        self.calls.append((list(states), questions, batch_size))
        return [{"answers": {QUESTION_ID: {"type": "noul", "noul": self.score_of(state)}}, "usage": {"truncated": False}}
                for state in states]


def _install_fake_sdk(monkeypatch, score_of) -> dict:
    """실제 SDK처럼 불러올 때 tokenizer 설정 파일을 고쳐 쓰는 가짜 laya, torch 모듈을 넣는다."""
    seen: dict = {"loads": [], "threads": [], "agents": []}

    def load(path, device=None, expected_sha256=None):
        tokenizer_config = Path(path) / "tokenizer" / "tokenizer_config.json"
        seen["loads"].append({"path": Path(path), "device": device, "expected_sha256": expected_sha256,
                              "tokenizer_config": tokenizer_config.read_bytes()})
        tokenizer_config.write_text('{"extra_special_tokens": {}}', encoding="utf-8")
        seen["agents"].append(_FakeAgent(score_of))
        return seen["agents"][-1]

    laya, torch = types.ModuleType("laya"), types.ModuleType("torch")
    laya.load, torch.set_num_threads, torch.Tensor = load, seen["threads"].append, type("Tensor", (), {})
    monkeypatch.setitem(sys.modules, "laya", laya)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return seen


def test_question_asks_one_yes_no_question_about_concrete_means():
    assert list(QUESTION) == [QUESTION_ID] and QUESTION[QUESTION_ID]["type"] == "noul"
    assert "수치 조건" in QUESTION[QUESTION_ID]["instructions"]
    assert compose_state("대상 문장이다.", "") == "대상 문장이다."
    assert compose_state("제목", " 제목 ") == "제목"  # 제목 자신이 후보일 때는 되풀이하지 않는다
    assert compose_state("대상 문장이다.", "융합 제어 구조") == "슬라이드 제목: 융합 제어 구조\n대상 문장: 대상 문장이다."


def test_model_folder_is_verified_before_loading_and_nothing_is_downloaded(tmp_path, monkeypatch):
    seen = _install_fake_sdk(monkeypatch, lambda state: 0.9)
    runtime_root = tmp_path / "data" / "laya_runtime"
    with pytest.raises(LayaUnavailable, match="자동으로 내려받지 않습니다"):
        LayaSecondStage(tmp_path / "models" / "laya-multilingual", runtime_root)
    model_dir, _manifest = _fake_model_dir(tmp_path)
    (model_dir / "tokenizer" / "tokenizer.json").write_bytes(b'{"tokenizer": 4}')
    with pytest.raises(LayaUnavailable, match="해시가 manifest와 다릅니다"):
        LayaSecondStage(model_dir, runtime_root)
    (model_dir / "tokenizer" / "tokenizer.json").unlink()
    with pytest.raises(LayaUnavailable, match="모델 파일이 없습니다"):
        LayaSecondStage(model_dir, runtime_root)
    assert seen["loads"] == [] and not runtime_root.exists()  # 확인에 실패하면 SDK를 부르지도, 사본을 만들지도 않는다


def test_without_the_laya_environment_the_error_says_where_to_run_it(tmp_path, monkeypatch):
    model_dir, _manifest = _fake_model_dir(tmp_path)
    monkeypatch.setitem(sys.modules, "laya", None)  # import laya가 ImportError를 낸다
    with pytest.raises(LayaUnavailable, match=r"\.venv-laya"):
        LayaSecondStage(model_dir, tmp_path / "runtime")


def test_adapter_loads_a_working_copy_on_cpu_and_keeps_the_verified_folder_untouched(tmp_path, monkeypatch):
    model_dir, manifest = _fake_model_dir(tmp_path)
    before = _tree(model_dir)
    seen = _install_fake_sdk(monkeypatch, lambda state: 0.9)
    runtime_root = tmp_path / "data" / "laya_runtime"

    first = LayaSecondStage(model_dir, runtime_root, threads=3)
    second = LayaSecondStage(model_dir, runtime_root, threads=3)

    assert first.model_version == second.model_version == "example/laya-test@0123456789ab"
    assert seen["threads"] == [3, 3] and len(seen["loads"]) == 2
    for load in seen["loads"]:
        assert load["device"] == "cpu" and load["path"] == runtime_root / "0123456789ab" and load["path"] != model_dir
        assert load["expected_sha256"] == {name: manifest["files"][name]["sha256"] for name in SDK_CHECKED}
        # SDK가 지난번에 고쳐 쓴 설정이 남아 있지 않다. 매번 확인된 원본에서 다시 복사한다
        assert load["tokenizer_config"] == b'{"extra_special_tokens": []}'
    assert (runtime_root / "0123456789ab" / "model.safetensors").read_bytes() == bytes(range(64))
    assert _tree(model_dir) == before  # SDK가 고쳐 쓴 것은 작업용 사본뿐이다


def test_adapter_turns_failed_or_malformed_batches_into_errors_and_keeps_going(tmp_path, monkeypatch):
    model_dir, _manifest = _fake_model_dir(tmp_path)
    scores = {"가": 0.5, "나": 0.49, "라": 0.8, "슬라이드 제목: 제목\n대상 문장: 마": 0.2, "바": float("nan"), "사": 0.7}

    def score_of(state):
        if state == "폭발":
            raise RuntimeError("boom")
        return scores[state]

    seen = _install_fake_sdk(monkeypatch, score_of)
    adapter = LayaSecondStage(model_dir, tmp_path / "runtime", batch_size=2, agree_at=0.5)
    results = adapter.assess_batch([("가", ""), ("나", ""), ("폭발", ""), ("라", ""), ("마", "제목"), ("사", ""), ("바", "")])

    assert [result.status for result in results] == ["ok", "ok", "error", "error", "ok", "ok", "error"]
    assert [result.decision for result in results] == ["AGREE", "DISAGREE", None, None, "DISAGREE", "AGREE", None]
    assert [result.error_code for result in results] == [None, None, "RuntimeError", "RuntimeError", None, None, "ValueError"]
    assert results[0].raw_scores == {"means": 0.5, "scale": "p_yes", "truncated": False} and results[2].raw_scores == {}
    assert all(result.model_version == "example/laya-test@0123456789ab" and result.question_version == "laya-q1"
               for result in results)
    # 한 번에 질문 하나만, 정해 둔 질문 그대로 묻는다
    calls = seen["agents"][0].calls
    assert [len(states) for states, _questions, _size in calls] == [2, 2, 2, 1]
    assert all(questions == QUESTION and size == len(states) for states, questions, size in calls)
    assert adapter.assess("가", "").decision == "AGREE"


# ---------------------------------------------------------------- 명령
def test_laya_compare_command_reports_problems_before_loading_and_runs_with_the_sdk(tmp_path, monkeypatch, capsys):
    from patent_marker.cli import main

    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for name in ("sample_report.pptx", "sample_report.pdf"):
        (inbox / name).write_bytes((FIXTURES / name).read_bytes())
    run_analysis(services, inbox, "run-001")
    export_run(services.database, services.config, "run-001", tmp_path / "outputs" / "run-001", ["jsonl", "annotated"])
    main_outputs = _tree(tmp_path / "outputs" / "run-001")
    config = tmp_path / "config.yaml"
    config.write_text("paths:\n  base_dir: .\nruntime:\n  offline: false\n", encoding="utf-8")
    command = ["laya-compare", "--config", str(config), "--run", "run-001"]
    seen = _install_fake_sdk(monkeypatch, lambda state: 0.05 if "예산" in state else 0.6)

    assert main(["laya-compare", "--config", str(config), "--run", "no-such-run"]) == 1
    assert "run을 찾을 수 없습니다" in capsys.readouterr().out
    assert main(command + ["--agree-at", "1.5"]) == 1
    assert "0과 1 사이" in capsys.readouterr().out
    assert main(command) == 1  # 모델을 받아 두지 않았다
    assert "Laya 모델이 없습니다" in capsys.readouterr().out and seen["loads"] == []

    model_dir, _manifest = _fake_model_dir(tmp_path)
    model_files = _tree(model_dir)
    assert main(command + ["--agree-at", "0.3"]) == 0
    out = capsys.readouterr().out
    assert "1차 후보 25개 중 동의 23개, 이견 2개, 판단 못 함 0개" in out and "실험 기능입니다" in out
    assert seen["loads"][0]["device"] == "cpu" and seen["loads"][0]["path"].parent == tmp_path / "data" / "laya_runtime"
    laya_dir = tmp_path / "outputs" / "run-001" / "laya"
    summary = json.loads((laya_dir / "laya_summary.json").read_text(encoding="utf-8"))
    assert summary["agree_at"] == 0.3 and summary["model_version"] == "example/laya-test@0123456789ab"
    assert sorted(path.name for path in laya_dir.iterdir()) == [
        "laya_report.html", "laya_results.jsonl", "laya_summary.json", "marked_laya_agree"]
    assert _tree(tmp_path / "outputs" / "run-001", skip="laya") == main_outputs and _tree(model_dir) == model_files


# ---------------------------------------------------------------- 실제 Laya 모델 (.venv-laya에서만)
@pytest.mark.requires_laya
def test_real_laya_runs_offline_on_cpu_and_leaves_the_model_folder_untouched(tmp_path):
    method = "Confidence 추정기 출력이 0.4 미만이면 가중치 제어기가 camera 가중치를 30% 줄이고 radar 가중치를 늘린다."
    admin = "다음 주 화요일 오전에 주간 회의를 진행한다."
    before = _tree(LAYA_MODEL_DIR)
    runtime.enter_offline_mode()
    attempts = len(runtime.network_attempts())

    adapter = LayaSecondStage(LAYA_MODEL_DIR, tmp_path / "laya_runtime", threads=2)
    first = adapter.assess_batch([(method, "융합 제어 구조"), (admin, "")])
    second = adapter.assess_batch([(method, "융합 제어 구조"), (admin, "")])

    assert [result.status for result in first] == ["ok", "ok"] and all(result.latency_ms > 0 for result in first)
    scores = [result.raw_scores[QUESTION_ID] for result in first]
    assert all(0.0 <= score <= 1.0 for score in scores) and scores[0] > scores[1]
    assert scores == pytest.approx([result.raw_scores[QUESTION_ID] for result in second], abs=1e-5)
    assert adapter.model_version.startswith("convaiinnovations/laya-multilingual@")
    assert len(runtime.network_attempts()) == attempts  # 외부 접속을 시도하지 않는다
    assert _tree(LAYA_MODEL_DIR) == before  # 받아 둔 모델 폴더는 그대로다
