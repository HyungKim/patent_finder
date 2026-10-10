"""train.bat의 본체(retrain) 시험: snapshot → 학습 → 평가 → 사람이 '예'라고 답해야 운영 모델이 바뀐다."""
from __future__ import annotations

import re

import pytest

from helpers import make_services, write_corpus
from patent_marker.analysis import run_analysis
from patent_marker.classifiers.registry import get_active
from patent_marker.feedback.events import record_feedback
from patent_marker.retrain import RetrainBlocked, retrain
from test_pipeline import RELAXED, _prepare


def _run(services, **kwargs):
    said: list[str] = []
    result = retrain(services, say=said.append, **kwargs)
    return result, "\n".join(said)


def test_retrain_trains_and_compares_but_changes_the_model_only_on_a_yes(tmp_path):
    services, inbox, _expected, _summary, _labeled = _prepare(tmp_path, **RELAXED)
    database = services.database

    result, text = _run(services, ask=lambda prompt: "")  # Enter만 치면 바꾸지 않는다
    assert result["model_version"] == "classifier-0001" and result["promoted"] is False
    assert get_active(database)["model_version"] == "classifier-0000"
    assert result["split"] == "test" and result["comparison"]["model_version"] == "classifier-0000"
    assert all(step in text for step in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"))
    assert re.search(r"지금 모델\s+classifier-0000\s", text) and re.search(r"새 모델\s+classifier-0001\s", text)
    assert "운영 모델은 그대로" in text
    assert database.query_one("SELECT COUNT(*) FROM evaluations")[0] == 2  # 새 모델과 지금 모델을 같은 문서로 평가

    def closed(prompt: str) -> str:  # 더블클릭 창의 입력이 닫혀 있으면 바꾸지 않는다
        raise EOFError

    result, _text = _run(services, ask=closed)
    assert result["model_version"] == "classifier-0002" and result["promoted"] is False
    assert get_active(database)["model_version"] == "classifier-0000"

    result, text = _run(services, ask=lambda prompt: "y")  # 완화한 기준을 채우므로 정식 승격
    assert result["model_version"] == "classifier-0003" and result["promoted"] and result["stage"] == "production"
    active = get_active(database)
    assert active["model_version"] == "classifier-0003" and active["stage"] == "PRODUCTION"
    assert "다음 분석(mark.bat)부터" in text and "rollback --model classifier-0000" in text
    run_analysis(services, inbox, "run-002")
    assert database.query_one("SELECT classifier_version FROM runs WHERE run_id = 'run-002'")[0] == "classifier-0003"
    snapshots = [row[0] for row in database.query("SELECT snapshot_id FROM label_snapshots ORDER BY created_at")]
    assert len(snapshots) == len(set(snapshots)) == 3  # 같은 초에 돌아도 snapshot 이름이 겹치지 않는다


def test_retrain_uses_the_pilot_stage_when_production_criteria_are_not_met(tmp_path):
    services, *_ = _prepare(tmp_path, families=6)
    result, text = _run(services, decision="yes")
    assert result["promoted"] and result["stage"] == "pilot" and result["production_blockers"]
    assert get_active(services.database)["stage"] == "PILOT" and "시범(PILOT)으로 올립니다" in text
    with pytest.raises(RetrainBlocked, match="승격 차단"):  # 정식 승격을 강제하면 기준을 낮추지 않고 막는다
        retrain(services, decision="yes", stage="production")
    assert get_active(services.database)["model_version"] == result["model_version"]


def test_retrain_is_blocked_until_both_labels_exist(tmp_path):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    expected = write_corpus(inbox, families=4)
    run_analysis(services, inbox, "run-001")
    with pytest.raises(RetrainBlocked, match="YES와 NO 판정이 모두"):
        retrain(services)
    for row in services.database.query("SELECT segment_id, normalized_text FROM segments"):
        if expected.get(row["normalized_text"]) == "YES":
            record_feedback(services.database, target_type="segment", target_id=row["segment_id"], label="YES",
                            reviewer_id="reviewer-01", guideline_version="1.0")
    with pytest.raises(RetrainBlocked, match="YES와 NO 판정이 모두"):
        retrain(services)
    assert get_active(services.database)["model_version"] == "classifier-0000"
