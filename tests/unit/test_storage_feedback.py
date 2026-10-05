"""저장소, 피드백 이력, 확정 라벨, 일괄 검토, snapshot·분할 시험 (스펙 9·10·12.1, 17.1)."""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from helpers import FIXTURES, make_services
from patent_marker.evaluation.splits import assign_partitions
from patent_marker.feedback.events import (FeedbackError, feedback_history, record_feedback, record_page_review,
                                           register_unparsed_positive)
from patent_marker.feedback.sampling import fetch_queue, rule_hints
from patent_marker.feedback.snapshot import SnapshotError, create_snapshot, load_snapshot, trainable_rows
from patent_marker.ingest import discover_files, ingest_file
from patent_marker.runtime import sha256_file
from patent_marker.storage import DatabaseError, open_database


@pytest.fixture
def ingested(services):
    result = ingest_file(services, FIXTURES / "sample_report.pptx")
    assert result.status == "partial" and result.segments == 25
    return services, result.document_id


def _segments(services, document_id, unit=None):
    sql = ("SELECT s.segment_id, s.normalized_text, p.unit FROM segments s JOIN paragraphs p USING (paragraph_id) "
           "WHERE s.document_id = ?")
    params = [document_id]
    if unit is not None:
        sql += " AND p.unit = ?"
        params.append(unit)
    return services.database.query(sql + " ORDER BY s.seq", params)


def _feedback(services, segment_id, label, reviewer="reviewer-01", **extra):
    return record_feedback(services.database, target_type="segment", target_id=segment_id, label=label,
                           reviewer_id=reviewer, guideline_version="1.0", **extra)


def _resolved(services, segment_id):
    row = services.database.query_one(
        "SELECT * FROM resolved_labels WHERE target_type = 'segment' AND target_id = ?", (segment_id,))
    return dict(row) if row else None


# ---------------------------------------------------------------- DB
def test_migrations_are_idempotent_and_foreign_keys_enforced(tmp_path):
    database = open_database(tmp_path / "db.sqlite3")
    assert database.applied_versions() == [1] and database.migrate() == []
    assert database.integrity() == {"integrity_ok": True, "foreign_key_violations": 0, "details": ["ok"]}
    with pytest.raises(sqlite3.IntegrityError):
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO paragraphs (paragraph_id, document_id, order_index, kind, original_text, "
                "source_locator_json, text_hash) VALUES ('p', 'missing-doc', 0, 'paragraph', 't', '{}', 'h')")


def test_transaction_rolls_back_on_error(tmp_path):
    database = open_database(tmp_path / "db.sqlite3")
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute("INSERT INTO system_state (key, value, updated_at) VALUES ('k', 'v', 't')")
            raise RuntimeError("중단")
    assert database.query_one("SELECT COUNT(*) FROM system_state")[0] == 0


def test_backup_and_restore_roundtrip(ingested, tmp_path):
    services, document_id = ingested
    backup = services.database.backup(tmp_path / "backup" / "copy.sqlite3")
    segment = _segments(services, document_id)[0]["segment_id"]
    _feedback(services, segment, "YES")
    assert services.database.query_one("SELECT COUNT(*) FROM feedback_events")[0] == 1
    saved = services.database.restore(backup)
    assert saved.exists()
    assert services.database.query_one("SELECT COUNT(*) FROM feedback_events")[0] == 0
    assert services.database.query_one("SELECT COUNT(*) FROM segments")[0] == 25
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a database")
    with pytest.raises((DatabaseError, sqlite3.DatabaseError)):
        services.database.restore(broken)


# ---------------------------------------------------------------- 수집
def test_reingest_is_idempotent_and_original_is_untouched(services, tmp_path):
    source = tmp_path / "copy.pptx"
    shutil.copy(FIXTURES / "sample_report.pptx", source)
    before = sha256_file(source)
    first = ingest_file(services, source)
    second = ingest_file(services, source)
    assert second.reused and second.document_id == first.document_id
    counts = [services.database.query_one(f"SELECT COUNT(*) FROM {table}")[0]
              for table in ("documents", "paragraphs", "segments")]
    assert counts == [1, 25, 25]  # 블록 27개 중 계층 불릿 3개가 한 문단으로 묶인다
    assert sha256_file(source) == before and sorted(p.name for p in tmp_path.glob("copy*")) == ["copy.pptx"]
    locator = json.loads(services.database.query_one(
        "SELECT source_locator_json FROM paragraphs WHERE kind = 'list_group'")[0])
    assert [part["locator"]["paragraph"] for part in locator["parts"]] == [0, 1, 2]  # 묶인 불릿의 원문 위치 보존


def test_unsupported_file_is_recorded_not_dropped(services, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    path = inbox / "old.hwp"
    path.write_bytes(b"hwp")
    (inbox / "~$lock.pptx").write_bytes(b"x")  # Office 잠금 파일은 건너뛴다
    result = ingest_file(services, path)
    assert result.status == "unsupported" and result.segments == 0
    row = services.database.query_one("SELECT parse_status, error FROM documents WHERE document_id = ?",
                                      (result.document_id,))
    assert row["parse_status"] == "unsupported" and "HWP" in row["error"]
    assert discover_files(inbox) == [path]


def test_unsegmentable_paragraph_is_reported_not_truncated(tmp_path):
    services = make_services(tmp_path, **{"segmentation.max_input_tokens": 20, "segmentation.target_tokens": 19,
                                           "segmentation.overlap_tokens": 2})
    path = tmp_path / "long.txt"
    path.write_text("짧은 문장.\n\n" + "임계값을 넘으면 출력을 낮추는 제어 " * 30, encoding="utf-8")
    result = ingest_file(services, path)
    row = services.database.query_one("SELECT parse_status, coverage_json, warnings_json FROM documents")
    assert result.status == row["parse_status"] == "partial"
    assert json.loads(row["coverage_json"])["segmentation_errors"] == 1
    assert any(w["code"] == "segmentation_error" for w in json.loads(row["warnings_json"]))
    flags = [json.loads(r["quality_flags"]) for r in services.database.query("SELECT quality_flags FROM paragraphs ORDER BY order_index")]
    assert flags == [[], ["segmentation_error"]] and result.segments == 1  # 잘라서 넣지 않고 오류로 남긴다


def test_revised_document_joins_the_same_family(services, tmp_path):
    original = tmp_path / "v1.txt"
    paragraphs = [f"측정값이 임계값 {i}을 넘으면 출력을 단계적으로 낮추는 제어를 적용한다." for i in range(8)]
    original.write_text("\n\n".join(paragraphs), encoding="utf-8")
    revised = tmp_path / "v2.txt"
    revised.write_text("\n\n".join(paragraphs[:6] + ["새로 추가한 문단으로 보정 계수를 갱신하는 절차를 설명한다."]), encoding="utf-8")
    other = tmp_path / "other.txt"
    other.write_text("전혀 다른 내용의 문서로 회의 일정을 정리한 메모이다.\n\n예산 집행 현황을 요약한 문단이다.", encoding="utf-8")
    ids = [ingest_file(services, path).document_id for path in (original, revised, other)]
    families = [services.database.query_one("SELECT document_family_id FROM documents WHERE document_id = ?", (i,))[0]
                for i in ids]
    assert families[0] == families[1] != families[2]


# ---------------------------------------------------------------- 피드백
def test_feedback_edits_are_new_events_with_supersedes(ingested):
    services, document_id = ingested
    segment = _segments(services, document_id)[3]["segment_id"]
    first = _feedback(services, segment, "NO")
    second = _feedback(services, segment, "YES", reason_codes=["CONCRETE_METHOD"], comment="조건-동작이 구체적")
    assert second["supersedes"] == first["feedback_id"]
    history = feedback_history(services.database, "segment", segment)
    assert [event["label"] for event in history] == ["NO", "YES"]  # 이력 보존
    assert history[1]["reason_codes"] == ["CONCRETE_METHOD"] and history[1]["review_context_hash"]
    resolved = _resolved(services, segment)
    assert resolved["label"] == "YES" and resolved["adjudication_status"] == "SINGLE_REVIEWER" and resolved["trainable"]
    assert history[0]["created_at"].endswith("Z")  # UTC ISO 8601


def test_reviewer_conflict_becomes_hold_until_adjudicated(ingested):
    services, document_id = ingested
    segment = _segments(services, document_id)[3]["segment_id"]
    _feedback(services, segment, "YES", reviewer="reviewer-01")
    _feedback(services, segment, "NO", reviewer="reviewer-02")
    resolved = _resolved(services, segment)
    assert resolved["label"] == "HOLD" and resolved["adjudication_status"] == "CONFLICT_PENDING"
    assert not resolved["trainable"]
    _feedback(services, segment, "NO", reviewer="reviewer-01")  # 같은 검토자의 수정은 최신이 유효
    assert _resolved(services, segment)["adjudication_status"] == "AGREED"
    _feedback(services, segment, "YES", reviewer="reviewer-02")
    _feedback(services, segment, "YES", reviewer="lead", is_adjudication=True, source="adjudication")
    resolved = _resolved(services, segment)
    assert resolved["label"] == "YES" and resolved["adjudication_status"] == "ADJUDICATED" and resolved["trainable"]


def test_hold_parse_errors_and_paragraph_labels_are_not_trainable(ingested):
    services, document_id = ingested
    rows = _segments(services, document_id)
    _feedback(services, rows[0]["segment_id"], "HOLD", reason_codes=["INSUFFICIENT_CONTEXT"])
    _feedback(services, rows[1]["segment_id"], "YES", reason_codes=["PARSING_ERROR"])
    _feedback(services, rows[2]["segment_id"], "NO", reason_codes=["WRONG_SPAN"])
    _feedback(services, rows[3]["segment_id"], "YES")
    paragraph = services.database.query_one("SELECT paragraph_id FROM paragraphs LIMIT 1")[0]
    record_feedback(services.database, target_type="paragraph", target_id=paragraph, label="YES",
                    reviewer_id="reviewer-01", guideline_version="1.0")
    trainable = trainable_rows(services.database)
    assert [row["segment_id"] for row in trainable] == [rows[3]["segment_id"]]
    assert services.database.query_one(
        "SELECT trainable FROM resolved_labels WHERE target_type = 'paragraph'")[0] == 0


def test_feedback_validation(ingested):
    services, document_id = ingested
    segment = _segments(services, document_id)[0]["segment_id"]
    with pytest.raises(FeedbackError):
        _feedback(services, segment, "MAYBE")
    with pytest.raises(FeedbackError):
        _feedback(services, segment, "YES", reason_codes=["NOT_A_CODE"])
    with pytest.raises(FeedbackError):
        _feedback(services, "no-such-segment", "YES")
    assert services.database.query_one("SELECT COUNT(*) FROM feedback_events")[0] == 0


def test_yes_on_unflagged_segment_is_recorded_as_manual_add(ingested):
    services, document_id = ingested
    segment = _segments(services, document_id)[4]["segment_id"]
    result = _feedback(services, segment, "YES")
    event = feedback_history(services.database, "segment", segment)[0]
    assert event["source"] == "manual_add" and event["prediction_id"] is None and result["resolved"]["trainable"]


def test_page_review_marks_selected_yes_and_rest_implicit_no(ingested):
    services, document_id = ingested
    slide = _segments(services, document_id, unit=3)
    yes_id, hold_id = slide[1]["segment_id"], slide[2]["segment_id"]
    result = record_page_review(services.database, document_id=document_id, unit=3, yes_segment_ids=[yes_id],
                                hold_segment_ids=[hold_id], reviewer_id="reviewer-01", guideline_version="1.0")
    assert result["recorded"] == {"YES": 1, "HOLD": 1, "NO": 1, "unchanged": 0}
    implicit = _resolved(services, slide[0]["segment_id"])
    assert implicit["label"] == "NO" and implicit["implicit"] == 1 and implicit["trainable"] == 1
    assert _resolved(services, yes_id)["implicit"] == 0
    batch = {event["batch_id"] for row in slide for event in feedback_history(services.database, "segment", row["segment_id"])}
    assert batch == {result["batch_id"]}
    # 같은 선택으로 다시 완료하면 이벤트가 늘지 않는다
    again = record_page_review(services.database, document_id=document_id, unit=3, yes_segment_ids=[yes_id],
                               hold_segment_ids=[hold_id], reviewer_id="reviewer-01", guideline_version="1.0")
    assert again["recorded"]["unchanged"] == 3
    assert services.database.query_one("SELECT COUNT(*) FROM feedback_events")[0] == 3
    # 다른 슬라이드에는 영향이 없다
    assert services.database.query_one(
        "SELECT COUNT(*) FROM resolved_labels rl JOIN segments s ON s.segment_id = rl.target_id "
        "JOIN paragraphs p USING (paragraph_id) WHERE p.unit != 3")[0] == 0
    with pytest.raises(FeedbackError):
        record_page_review(services.database, document_id=document_id, unit=3,
                           yes_segment_ids=[_segments(services, document_id, unit=2)[0]["segment_id"]],
                           reviewer_id="reviewer-01", guideline_version="1.0")


def test_unparsed_positive_registration(ingested):
    services, document_id = ingested
    item = register_unparsed_positive(services.database, document_id=document_id, unit=6,
                                      note="이미지 안의 제어 블록도", reviewer_id="reviewer-01")
    assert item.startswith("miss-")
    with pytest.raises(FeedbackError):
        register_unparsed_positive(services.database, document_id=document_id, unit=6, note=" ", reviewer_id="r")


# ---------------------------------------------------------------- 검토 큐·규칙 제안
def test_rule_hints_are_suggestions_only():
    score, tags = rule_hints("신뢰도가 0.4 미만이면 가중치를 높이는 제어를 적용한다.")
    assert score > 0 and {"CONDITION_ACTION", "NUMERIC_CONDITION", "METHOD_VERB"} <= set(tags)
    admin_score, admin_tags = rule_hints("다음 분기 회의 일정과 예산을 정리한다.")
    assert admin_score < 0 and admin_tags == ["ADMIN_CUE"]


def test_queue_filters_without_predictions(ingested):
    services, document_id = ingested
    assert fetch_queue(services.database, "candidates")["total"] == 0  # 예측 전에는 후보가 없다
    everything = fetch_queue(services.database, "all", limit=100)
    assert everything["total"] == 25 and all(item["final_decision"] is None for item in everything["items"])
    suggested = fetch_queue(services.database, "rule_suggest", limit=5)["items"]
    assert suggested[0]["queue_reason"] == "rule" and suggested[0]["rule_hint_score"] > 0
    segment = everything["items"][0]["segment_id"]
    _feedback(services, segment, "HOLD")
    assert [item["segment_id"] for item in fetch_queue(services.database, "hold")["items"]] == [segment]
    assert fetch_queue(services.database, "unreviewed", limit=100)["total"] == 24
    with pytest.raises(ValueError):
        fetch_queue(services.database, "unknown")


# ---------------------------------------------------------------- 분할·snapshot
def test_split_assignment_is_sticky_and_group_exclusive(services):
    groups = {f"fam-{i:02d}": {"n": 10, "pos": 4} for i in range(20)}
    first = assign_partitions(services.database, groups, [0.6, 0.2, 0.2], seed=42, split_tag="v1")
    counts = {name: sum(1 for value in first.values() if value == name) for name in ("train", "validation", "test")}
    assert counts == {"train": 12, "validation": 4, "test": 4}
    groups.update({f"new-{i}": {"n": 10, "pos": 4} for i in range(5)})
    second = assign_partitions(services.database, groups, [0.6, 0.2, 0.2], seed=42, split_tag="v2")
    assert all(second[group] == partition for group, partition in first.items())  # 기존 배정 불변
    assert len(second) == 25


def test_snapshot_is_immutable_and_excludes_unusable_labels(ingested, tmp_path):
    services, document_id = ingested
    rows = _segments(services, document_id)
    for index, row in enumerate(rows[:6]):
        _feedback(services, row["segment_id"], "YES" if index % 2 else "NO")
    _feedback(services, rows[6]["segment_id"], "HOLD")
    result = create_snapshot(services.database, services.config, "labels-v1")
    assert result["stats"]["labels"] == 6 and result["stats"]["yes"] == 3 and result["split_tag"] == "v1"
    loaded = load_snapshot(services.database, result["path"])
    assert {row["partition"] for row in loaded["rows"]} == {"train"}  # 문서 계열 하나 → 한 partition
    assert all(row["label"] in ("YES", "NO") for row in loaded["rows"])
    with pytest.raises(SnapshotError):
        create_snapshot(services.database, services.config, "labels-v1")  # 덮어쓰지 않는다
    path = tmp_path / "data" / "snapshots" / "labels-v1.jsonl"
    path.write_text(path.read_text(encoding="utf-8").replace('"label":"NO"', '"label":"YES"', 1), encoding="utf-8")
    with pytest.raises(SnapshotError):
        load_snapshot(services.database, path)  # 내용이 바뀐 snapshot은 거부


def test_snapshot_requires_labels(services):
    with pytest.raises(SnapshotError):
        create_snapshot(services.database, services.config, "labels-v1")
    assert make_services  # 가져온 도우미 사용 표시
