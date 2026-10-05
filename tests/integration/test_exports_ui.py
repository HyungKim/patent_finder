"""결과물(HTML, JSONL, PPTX/PDF 사본)과 로컬 검토 화면 API 시험 (스펙 13절, 17.1)."""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from build_synthetic import PDF_YES, YES_TABLE_CELL, YES_WEIGHT
from helpers import FIXTURES, make_services
from patent_marker.analysis import run_analysis
from patent_marker.export.runner import export_run
from patent_marker.feedback.events import record_feedback, record_page_review
from patent_marker.runtime import sha256_file
from patent_marker.ui.server import create_server

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _label_only(services, document_id: str, yes_prefixes: list[str]) -> list[str]:
    """지정한 문장으로 시작하는 segment만 YES, 문서의 나머지는 모두 NO로 일괄 검토한다."""
    rows = services.database.query(
        "SELECT s.segment_id, s.normalized_text, p.unit, p.section_id FROM segments s "
        "JOIN paragraphs p USING (paragraph_id) WHERE s.document_id = ? ORDER BY s.seq", (document_id,))
    groups: dict[tuple, list] = {}
    for row in rows:
        groups.setdefault((row["unit"], None if row["unit"] is not None else row["section_id"]), []).append(row)
    chosen = []
    for (unit, section), members in groups.items():
        yes = [row["segment_id"] for row in members
               if any(prefix in row["normalized_text"] for prefix in yes_prefixes)]
        chosen.extend(yes)
        record_page_review(services.database, document_id=document_id, unit=unit, section_id=section,
                           yes_segment_ids=yes, reviewer_id="reviewer-01", guideline_version="1.0")
    return chosen


def _document_id(services, name: str) -> str:
    return services.database.query_one("SELECT document_id FROM documents WHERE file_name = ?", (name,))[0]


@pytest.fixture
def analyzed(tmp_path):
    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for path in FIXTURES.iterdir():
        (inbox / path.name).write_bytes(path.read_bytes())
    (inbox / "danger.txt").write_text(
        "<script>alert(1)</script> 값이 임계값을 넘으면 <b>출력</b>을 낮추는 제어를 \"적용\"한다 & 기록한다.\n",
        encoding="utf-8")
    (inbox / "legacy.hwp").write_bytes(b"hwp")
    run_analysis(services, inbox, "run-001")
    return services, inbox, tmp_path / "outputs" / "run-001"


# ---------------------------------------------------------------- HTML · JSONL
def test_html_report_is_self_contained_and_escapes_document_text(analyzed):
    services, inbox, output = analyzed
    result = export_run(services.database, services.config, "run-001", output, ["html", "jsonl"])
    html = Path(result["files"]["html"]["path"]).read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)" not in html
    assert "&lt;b&gt;출력&lt;/b&gt;" in html and "&amp; 기록한다" in html
    assert not re.search(r"https?://", html)  # CDN·외부 글꼴·추적 없음
    assert not re.search(r"<(link|img|iframe)\b", html) and "src=" not in html
    assert "default-src 'none'" in html
    assert "임시 후보 표시 (SEED · 미검증)" in html and "특허 등록 확률이 아닙니다" in html
    assert "legacy.hwp" in html and "HWP 형식은 지원하지 않습니다" in html  # 미지원 파일은 오류 목록에
    assert "OCR 필요" in html and "후보만 보기" not in html and "표시된 구간만 보기" in html
    assert result["problems"] == [{"file": "legacy.hwp", "status": "unsupported",
                                   "reason": "HWP 형식은 지원하지 않습니다. PDF 또는 DOCX로 변환한 뒤 다시 시도하세요."}]


def test_jsonl_rows_follow_schema_and_keep_system_and_human_fields_apart(analyzed):
    services, inbox, output = analyzed
    segment = services.database.query_one(
        "SELECT segment_id FROM segments WHERE normalized_text LIKE ?", (YES_WEIGHT[:20] + "%",))[0]
    record_feedback(services.database, target_type="segment", target_id=segment, label="YES",
                    reviewer_id="reviewer-01", guideline_version="1.0", reason_codes=["CONCRETE_METHOD"])
    result = export_run(services.database, services.config, "run-001", output, ["jsonl"])
    rows = [json.loads(line) for line in Path(result["files"]["jsonl"]["path"]).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == services.database.query_one("SELECT COUNT(*) FROM segments")[0]
    required = {"schema_version", "document_id", "paragraph_id", "segment_id", "source_locator", "original_text",
                "input_hash", "embedding_ref", "prediction_id", "encoder_revision", "classifier_version",
                "stage1_score", "stage1_threshold", "stage1_decision", "laya", "final_decision", "feedback"}
    for row in rows:
        assert required <= set(row) and row["schema_version"] == 1 and row["laya"] == {"mode": "off"}
        assert row["input_hash"].startswith("sha256:") and re.fullmatch(r"embeddings/.+\.npy#row=\d+", row["embedding_ref"])
        assert 0.0 <= row["stage1_score"] <= 1.0 and row["stage1_decision"] in ("CANDIDATE", "NOT_CANDIDATE")
    labelled = next(row for row in rows if row["segment_id"] == segment)
    assert labelled["feedback"] == {"label": "YES", "reason_codes": ["CONCRETE_METHOD"], "reviewer_id": "reviewer-01",
                                    "adjudication_status": "SINGLE_REVIEWER", "implicit": False}
    assert labelled["source_locator"]["parts"][0]["locator"]["slide"] == 3
    assert sum(row["feedback"] is None for row in rows) == len(rows) - 1


# ---------------------------------------------------------------- PPTX 사본
def test_pptx_copy_marks_the_right_shapes_and_keeps_valid_run_properties(analyzed):
    from pptx import Presentation

    services, inbox, output = analyzed
    document_id = _document_id(services, "sample_report.pptx")
    _label_only(services, document_id, [YES_WEIGHT, YES_TABLE_CELL])
    before = sha256_file(inbox / "sample_report.pptx")
    result = export_run(services.database, services.config, "run-001", output, ["annotated"])
    entry = next(item for item in result["annotated"] if item["file_name"] == "sample_report.pptx")
    assert entry["status"] == "ok" and entry["marked"] == 2 and entry["skipped"] == []
    assert sha256_file(inbox / "sample_report.pptx") == before

    presentation = Presentation(entry["output"])
    slides = list(presentation.slides)
    assert len(slides) == 9  # 요약 슬라이드 한 장 추가
    marks = {index: [shape.name for shape in slide.shapes if shape.name.startswith("PM_MARK")]
             for index, slide in enumerate(slides, start=1)}
    assert marks[3] == ["PM_MARK_OUTLINE", "PM_MARK_TAG"] and marks[4] == ["PM_MARK_OUTLINE", "PM_MARK_TAG"]
    assert all(not names for index, names in marks.items() if index not in (3, 4))
    highlights = {index: len(list(slide._element.iter(A + "highlight"))) for index, slide in enumerate(slides, start=1)}
    assert highlights[3] == 3 and highlights[4] == 3  # 묶인 불릿 3개, 표 행의 셀 3개
    assert sum(highlights.values()) == 6
    tags = [shape.text_frame.text for shape in slides[2].shapes if shape.name == "PM_MARK_TAG"]
    assert len(tags) == 1 and "#1" in tags[0]
    summary = "\n".join(shape.text_frame.text for shape in slides[8].shapes if shape.has_text_frame)
    assert "특허 검토 후보 요약" in summary and "#1 · 슬라이드 3" in summary and "#2 · 슬라이드 4 · 표 2행" in summary
    # a:rPr 안에서 highlight는 채움·효과 뒤, 글꼴 지정 앞에 와야 한다 (스키마 순서)
    before_tags = {A + name for name in ("ln", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill",
                                         "effectLst", "effectDag")}
    for slide in slides:
        for properties in slide._element.iter(A + "rPr"):
            children = [child.tag for child in properties]
            if A + "highlight" in children:
                position = children.index(A + "highlight")
                assert all(tag in before_tags for tag in children[:position])
                assert not any(tag in before_tags for tag in children[position + 1:])
    # 원본에는 표시가 없다
    original = Presentation(str(inbox / "sample_report.pptx"))
    assert not any(shape.name.startswith("PM_") for slide in original.slides for shape in slide.shapes)


# ---------------------------------------------------------------- PDF 사본
def test_pdf_copy_highlights_cover_exactly_the_marked_text(analyzed):
    import pdfplumber

    services, inbox, output = analyzed
    document_id = _document_id(services, "sample_report.pdf")
    _label_only(services, document_id, [PDF_YES[:25], "유량 밸브"])
    result = export_run(services.database, services.config, "run-001", output, ["annotated"])
    entry = next(item for item in result["annotated"] if item["file_name"] == "sample_report.pdf")
    assert entry["status"] == "ok" and entry["marked"] == 2 and entry["skipped"] == []
    found = {}
    with pdfplumber.open(entry["output"]) as pdf:
        for page in pdf.pages:
            for annotation in page.annots:
                box = (annotation["x0"] - 0.5, annotation["top"] - 0.5, annotation["x1"] + 0.5, annotation["bottom"] + 0.5)
                found[page.page_number] = (annotation["contents"], page.crop(box).extract_text())
    assert set(found) == {1, 3}
    assert "#1" in found[1][0] and found[1][1].replace("\n", " ").replace(" ", "") == PDF_YES.replace(" ", "")
    assert "#2" in found[3][0] and "편차 3도 초과 시 개도를 편차에 비례해 증가" in found[3][1]
    with pdfplumber.open(str(inbox / "sample_report.pdf")) as pdf:
        assert not any(page.annots for page in pdf.pages)  # 원본에는 주석이 없다


def test_rotated_pdf_is_not_painted_when_position_is_uncertain(tmp_path):
    from test_parsers import _rotated_upright_pdf

    services = make_services(tmp_path)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    _rotated_upright_pdf(inbox / "rotated.pdf")
    run_analysis(services, inbox, "run-001")
    rows = services.database.query("SELECT segment_id FROM segments")
    assert len(rows) == 1
    for row in rows:
        record_feedback(services.database, target_type="segment", target_id=row["segment_id"], label="YES",
                        reviewer_id="reviewer-01", guideline_version="1.0")
    result = export_run(services.database, services.config, "run-001", tmp_path / "out", ["annotated"])
    entry = result["annotated"][0]
    assert entry["status"] == "ok" and entry["marked"] == 0 and len(entry["skipped"]) == 1
    assert all("회전" in item["reason"] for item in entry["skipped"])  # 잘못 칠하지 않고 사유를 남긴다


# ---------------------------------------------------------------- 검토 화면 API
@pytest.fixture
def server(analyzed):
    services, _inbox, _output = analyzed
    httpd, app = create_server(services.database, services.config, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield services, f"http://127.0.0.1:{httpd.server_address[1]}", app.token
    httpd.shutdown()
    httpd.server_close()


def _call(base: str, path: str, body: dict | None = None, token: str | None = None, host: str | None = None):
    request = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"))
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-PM-Token", token)
    if host:
        request.add_header("Host", host)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers)


def test_review_server_serves_bundled_assets_with_security_headers(server):
    _services, base, token = server
    status, body, headers = _call(base, "/")
    page = body.decode("utf-8")
    assert status == 200 and token in page and "{{PM_TOKEN}}" not in page
    assert not re.search(r"https?://", page)  # 외부 자원 없음
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"
    for asset in ("/static/app.js", "/static/app.css"):
        status, content, _ = _call(base, asset)
        assert status == 200 and content and not re.search(rb"https?://", content)
    assert _call(base, "/static/../server.py")[0] == 404 and _call(base, "/api/nope")[0] == 404


def test_review_server_rejects_foreign_host_and_missing_token(server):
    services, base, token = server
    assert _call(base, "/api/status", host="evil.example.com")[0] == 403  # DNS rebinding 방어
    segment = services.database.query_one("SELECT segment_id FROM segments LIMIT 1")[0]
    body = {"target_type": "segment", "target_id": segment, "label": "YES"}
    assert _call(base, "/api/feedback", body)[0] == 403
    assert _call(base, "/api/feedback", body, token="wrong")[0] == 403
    assert services.database.query_one("SELECT COUNT(*) FROM feedback_events")[0] == 0
    status, payload, _ = _call(base, "/api/feedback", {**body, "label": "MAYBE"}, token=token)
    assert status == 400 and "label" in json.loads(payload)["error"]
    with pytest.raises(ValueError):
        create_server(services.database, services.config, host="0.0.0.0", port=0)


def test_review_flow_through_the_api(server):
    services, base, token = server
    status = json.loads(_call(base, "/api/status")[1])
    assert status["model"]["stage"] == "SEED" and status["model"]["policy_status"] == "SEED_UNVALIDATED"
    assert status["counts"]["unreviewed"] == status["counts"]["segments"] and not status["retraining"]["suggest"]
    documents = json.loads(_call(base, "/api/documents")[1])
    names = {doc["file_name"]: doc for doc in documents}
    assert names["legacy.hwp"]["parse_status"] == "unsupported" and names["sample_report.pptx"]["segments"] == 25
    deck = names["sample_report.pptx"]["document_id"]
    detail = json.loads(_call(base, f"/api/document?id={deck}")[1])
    assert [group["key"]["unit"] for group in detail["groups"]] == [1, 2, 3, 4, 5, 7, 8]
    slide3 = detail["groups"][2]
    target = slide3["segments"][1]
    assert "".join(piece["text"] for piece in target["pieces"] if piece["target"]).startswith(YES_WEIGHT)
    assert target["location"].startswith("슬라이드 3") and target["score"] is not None

    review = {"document_id": deck, "unit": 3, "section_id": None, "yes_segment_ids": [target["segment_id"]],
              "hold_segment_ids": [], "predictions": {target["segment_id"]: target["prediction_id"]}}
    status_code, payload, _ = _call(base, "/api/page-review", review, token=token)
    assert status_code == 200 and json.loads(payload)["recorded"] == {"YES": 1, "HOLD": 0, "NO": 2, "unchanged": 0}
    detail = json.loads(_call(base, f"/api/document?id={deck}")[1])
    assert detail["groups"][2]["reviewed"] and not detail["groups"][3]["reviewed"]
    labels = [(s["human_label"], s["label_implicit"]) for s in detail["groups"][2]["segments"]]
    assert labels == [("NO", True), ("YES", False), ("NO", True)]

    other = detail["groups"][3]["segments"][0]
    status_code, payload, _ = _call(base, "/api/feedback", {
        "target_type": "segment", "target_id": other["segment_id"], "label": "HOLD",
        "reason_codes": ["INSUFFICIENT_CONTEXT"], "comment": "앞 슬라이드 확인 필요"}, token=token)
    assert status_code == 200 and json.loads(payload)["resolved"]["label"] == "HOLD"
    history = json.loads(_call(base, f"/api/history?target_type=segment&target_id={other['segment_id']}")[1])
    assert history[0]["comment"] == "앞 슬라이드 확인 필요" and history[0]["reviewer_id"] == "reviewer-01"

    hold_queue = json.loads(_call(base, "/api/queue?filter=hold")[1])
    assert hold_queue["total"] == 1 and hold_queue["items"][0]["segment_id"] == other["segment_id"]
    candidates = json.loads(_call(base, "/api/queue?filter=candidates&limit=200")[1])
    assert all(item["final_decision"] == "CANDIDATE" for item in candidates["items"])
    assert _call(base, "/api/queue?filter=bogus")[0] == 400

    status_code, payload, _ = _call(base, "/api/unparsed", {"document_id": deck, "unit": 6, "note": "이미지 안의 블록도"},
                                    token=token)
    assert status_code == 200
    detail = json.loads(_call(base, f"/api/document?id={deck}")[1])
    assert detail["unparsed_positives"][0]["note"] == "이미지 안의 블록도"
    counts = json.loads(_call(base, "/api/status")[1])["counts"]
    assert counts["labels"] == {"YES": 1, "NO": 2, "HOLD": 1} and counts["trainable"] == 3
