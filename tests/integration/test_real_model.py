"""실제 E5(ONNX) 모델을 쓰는 시험: 오프라인, CPU 전용, 입력 길이, 재현성 (스펙 2·6절, 17.1).

models/multilingual-e5-small 이 없으면 사유와 함께 건너뛴다.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from build_synthetic import DOCX_YES, PDF_YES, YES_GROUP_TEXT, YES_TABLE_CELL, YES_WEIGHT
from helpers import FIXTURES, MODEL_DIR
from patent_marker import runtime
from patent_marker.cli import main
from patent_marker.config import SegmentationConfig
from patent_marker.embeddings.e5 import InputTooLongError
from patent_marker.parsers.base import Block
from patent_marker.segmentation.paragraphs import build_paragraphs
from patent_marker.segmentation.segmenter import segment_paragraph

pytestmark = pytest.mark.requires_model


def test_embeddings_are_cpu_float32_normalized_and_deterministic(real_services):
    import onnxruntime

    encoder = real_services.encoder
    assert encoder.providers == ["CPUExecutionProvider"]  # GPU/CoreML provider가 있어도 CPU만 사용
    assert "CPUExecutionProvider" in onnxruntime.get_available_providers()
    texts = ["Radar confidence가 임계값보다 낮으면 camera feature의 가중치를 증가시킨다.",
             "다음 분기에 센서 융합 성능 개선을 추진한다.", "3.5 mA < 4 MA"]
    first = encoder.encode(texts)
    assert first.shape == (3, 384) and first.dtype == np.float32
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0, atol=1e-5)
    assert np.array_equal(first, encoder.encode(texts))  # 같은 입력 → 같은 출력
    single = np.vstack([encoder.encode([text]) for text in texts])
    assert np.allclose(first, single, atol=1e-5)  # 배치 구성과 무관
    assert float(first[0] @ first[1]) < 0.95  # 서로 다른 문장은 구분된다
    assert encoder.config_hash and encoder.encoder_revision == "614241f622f53c4eeff9890bdc4f31cfecc418b3"


def test_too_long_input_raises_instead_of_being_truncated(real_services):
    with pytest.raises(InputTooLongError):
        real_services.encoder.encode(["임계값을 넘으면 출력을 낮춘다. " * 200])


def test_real_tokenizer_keeps_every_segment_within_the_model_limit(real_services):
    tokenizer = real_services.tokenizer
    assert tokenizer.overhead == 6  # <s> + "query: " + </s>
    text = "Radar confidence가 0.4 미만이면 camera branch gain을 1.5배로 높이고 3.5 mA 이하에서 복귀한다. " * 120
    block = Block(kind="paragraph", text=text, locator={}, section_key="s")
    config = SegmentationConfig()
    paragraph = build_paragraphs([block], tokenizer, config)[0]
    drafts = segment_paragraph(paragraph, tokenizer, config)
    assert len(drafts) > 5
    for draft in drafts:
        assert draft.token_count <= config.target_tokens
        assert len(tokenizer.encode_inputs([draft.text])[0]) <= 512  # 접두사·특수 토큰 포함
        start, end = draft.spans[0]
        assert text[start:end] == draft.text  # 원문 구간 그대로
    covered = sum(end - start for draft in drafts for start, end in draft.spans)
    assert covered >= len(text.replace(" ", "")) * 0.99  # 조용히 버린 내용이 없다
    real_services.encoder.encode([draft.text for draft in drafts[:4]])
    offsets = tokenizer.offsets("가중치 조절 weight")
    assert offsets[0][0] == 0 and offsets[-1][1] == len("가중치 조절 weight")


@pytest.fixture
def cli_env(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "paths:\n  base_dir: .\n  e5: " + json.dumps(str(MODEL_DIR)) + "\n"
        "review:\n  reviewer_id: reviewer-01\n", encoding="utf-8")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for path in FIXTURES.iterdir():
        (inbox / path.name).write_bytes(path.read_bytes())
    return tmp_path, str(config), inbox


def test_cli_analyze_runs_fully_offline_and_marks_known_candidates(cli_env, capsys):
    base, config, inbox = cli_env
    attempts_before = len(runtime.network_attempts())
    output = base / "outputs" / "run-001"
    assert main(["analyze", "--config", config, "--input", str(inbox), "--output", str(output)]) == 0
    assert runtime.network_guard_installed()
    assert len(runtime.network_attempts()) == attempts_before  # 분석 중 네트워크 접근 시도가 없다
    printed = capsys.readouterr().out
    assert "임시 후보(SEED · 미검증)" in printed and "예산 8.0GiB 이내" in printed

    rows = [json.loads(line) for line in (output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 64 and {row["model_stage"] for row in rows} == {"SEED"}
    assert {row["policy_status"] for row in rows} == {"SEED_UNVALIDATED"}

    def decision(fragment: str) -> str:
        return next(row["final_decision"] for row in rows if fragment in row["original_text"])

    for fragment in (YES_WEIGHT, YES_TABLE_CELL, YES_GROUP_TEXT, PDF_YES[:30], DOCX_YES[:30]):
        assert decision(fragment) == "CANDIDATE"
    for fragment in ("회의는 매주 화요일에 진행한다.", "4분기: 통합 평가 및 최종 보고 예정", "추진 일정 및 예산"):
        assert decision(fragment) == "NOT_CANDIDATE"
    assert (output / "report.html").is_file()
    assert sorted(p.name for p in (output / "marked").iterdir()) == ["sample_report.marked.pdf", "sample_report.marked.pptx"]
    details = json.loads((output / "export_summary.json").read_text(encoding="utf-8"))
    assert details["model_stage"] == "SEED" and details["problems"] == []

    assert main(["verify-run", "--config", config, "--run", "run-001"]) == 0  # 과거 판정 재현
    assert "재현됨" in capsys.readouterr().out
    assert main(["analyze", "--config", config, "--input", str(inbox), "--output", str(output)]) == 1  # 완료된 run
    assert "이미 완료된 run" in capsys.readouterr().out
    assert main(["export", "--config", config, "--run", "run-001", "--format", "jsonl",
                 "--output", str(base / "outputs" / "again")]) == 0
    assert (base / "outputs" / "again" / "results.jsonl").is_file()


def test_cli_mark_analyzes_dropped_files_into_new_run_folders(cli_env, capsys, monkeypatch):
    base, config, inbox = cli_env
    monkeypatch.setenv("PM_NO_OPEN", "1")
    dropped = base / "끌어온자료"
    dropped.mkdir()
    amp = dropped / "R&D현황.pptx"
    amp.write_bytes((inbox / "sample_report.pptx").read_bytes())
    pdf = inbox / "sample_report.pdf"
    # cmd가 & 앞에서 자른 이름을 넘겼을 때: 명령줄 전체(PM_CMDLINE)에서 원래 이름을 되살린다
    monkeypatch.setenv("PM_CMDLINE", f'cmd /c ""D:\\pf\\mark.bat" {amp} {pdf}"')
    assert main(["mark", "--config", config, str(dropped / "R")]) == 0
    assert "결과 폴더:" in capsys.readouterr().out
    runs = sorted((base / "outputs").glob("mark-*"))
    assert len(runs) == 1 and (runs[0] / "report.html").is_file()
    assert sorted(p.name for p in (runs[0] / "marked").iterdir()) == ["R&D현황.marked.pptx", "sample_report.marked.pdf"]
    assert runtime.sha256_file(amp) == runtime.sha256_file(inbox / "sample_report.pptx")  # 원본은 그대로

    monkeypatch.delenv("PM_CMDLINE")
    assert main(["mark", "--config", config, str(inbox)]) == 0  # 폴더째로. 바로 이어 실행해도 다른 run 폴더를 쓴다
    assert len(list((base / "outputs").glob("mark-*"))) == 2


def test_cli_doctor_status_backup_and_blocked_training(cli_env, capsys):
    base, config, inbox = cli_env
    assert main(["doctor", "--offline", "--config", config]) == 0
    report = capsys.readouterr().out
    assert "[FAIL]" not in report and "CPUExecutionProvider" in report and "해시 일치" in report
    assert "네트워크 차단 가드: 설치됨, 접속 시도 없음" in report

    assert main(["ingest", "--config", config, "--input", str(inbox)]) == 0
    assert "partial" in capsys.readouterr().out
    assert main(["status", "--config", config]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["model"] is None  # ingest만으로는 운영 모델이 생기지 않는다
    assert status["counts"]["documents"] == 5 and status["counts"]["segments"] == 64
    assert status["counts"]["unreviewed"] == 64 and status["laya_mode"] == "off"
    assert main(["snapshot", "--config", config, "--name", "labels-v1"]) == 1  # 라벨이 없으면 snapshot 불가
    assert "확정 YES/NO 라벨이 없습니다" in capsys.readouterr().out
    backup = base / "backup" / "db.sqlite3"
    assert main(["backup", "--config", config, "--output", str(backup)]) == 0 and backup.is_file()
    assert main(["restore", "--config", config, "--from", str(backup)]) == 0
    assert main(["rollback", "--config", config, "--model", "classifier-9999"]) == 1
    assert main(["promote", "--config", config, "--model", "classifier-9999", "--report", str(base / "none.json")]) == 2
    assert "승격 차단" in capsys.readouterr().out


def test_cli_rejects_missing_model_and_bad_config(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("paths:\n  base_dir: .\n  e5: models/not-there\n", encoding="utf-8")
    (tmp_path / "doc.txt").write_text("임계값을 넘으면 출력을 낮춘다.", encoding="utf-8")
    attempts_before = len(runtime.network_attempts())
    code = main(["analyze", "--config", str(config), "--input", str(tmp_path / "doc.txt"),
                 "--output", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1 and "로컬 모델 디렉터리가 없습니다" in out and "자동으로 내려받지 않습니다" in out
    assert len(runtime.network_attempts()) == attempts_before
    config.write_text("runtime:\n  device: cuda\n", encoding="utf-8")
    assert main(["status", "--config", str(config)]) == 1
    assert "cpu" in capsys.readouterr().out
    assert Path(MODEL_DIR).is_dir()
