"""로컬 리뷰 화면 서버 (스펙 2절, 13절).

- 127.0.0.1에만 바인딩한다. 외부 API가 아니며 정적 자원은 패키지에 함께 들어 있다(CDN·외부 글꼴 없음).
- Host 헤더를 확인해 DNS rebinding을 막고, 쓰기 요청에는 실행마다 새로 만드는 토큰을 요구한다.
- 표준 라이브러리만 사용한다.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..classifiers.registry import get_active, get_model
from ..config import AppConfig
from ..export.data import location_label
from ..feedback.events import (FeedbackError, feedback_history, record_feedback, record_page_review,
                               register_unparsed_positive)
from ..feedback.resolution import REASON_CODES
from ..feedback.sampling import FILTERS, fetch_queue, fetch_segments
from ..policies.thresholds import get_policy
from ..storage import Database

_MAX_BODY = 1 << 20
_STATIC = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class ReviewApp:
    """HTTP와 무관한 리뷰 기능. 시험에서 직접 호출할 수 있다."""

    def __init__(self, database: Database, config: AppConfig) -> None:
        self.database = database
        self.config = config
        self.token = secrets.token_urlsafe(24)

    # ---- 조회 ----
    def status(self) -> dict[str, Any]:
        database = self.database
        active = get_active(database)
        model = None
        if active:
            policy = get_policy(database, active.get("policy_version")) or {}
            registered = get_model(database, active["model_version"]) or {}
            model = {**active, "policy_status": policy.get("status"), "threshold": policy.get("threshold"),
                     "kind": registered.get("kind")}
        labels = {row["label"]: row["n"] for row in database.query(
            "SELECT label, COUNT(*) AS n FROM resolved_labels WHERE target_type = 'segment' GROUP BY label")}
        segments = database.query_one(
            "SELECT COUNT(*) FROM segments s JOIN documents d ON d.document_id = s.document_id WHERE d.is_current = 1")[0]
        reviewed = database.query_one(
            "SELECT COUNT(*) FROM resolved_labels rl JOIN segments s ON s.segment_id = rl.target_id "
            "JOIN documents d ON d.document_id = s.document_id WHERE rl.target_type = 'segment' AND d.is_current = 1")[0]
        trainable = database.query_one(
            "SELECT COUNT(*) FROM resolved_labels WHERE target_type = 'segment' AND trainable = 1")[0]
        last = database.query_one("SELECT value FROM system_state WHERE key = 'labels_at_last_training'")
        new_labels = trainable - (int(last["value"]) if last else 0)
        threshold = self.config.retraining.suggest_after_new_labels
        return {
            "reviewer_id": self.config.reviewer_id,
            "guideline_version": self.config.review.label_guideline_version,
            "model": model,
            "counts": {
                "documents": database.query_one("SELECT COUNT(*) FROM documents WHERE is_current = 1")[0],
                "segments": segments, "labels": labels, "unreviewed": segments - reviewed,
                "trainable": trainable,
            },
            "retraining": {"new_labels": new_labels, "suggest_after": threshold, "suggest": new_labels >= threshold},
            "reason_codes": list(REASON_CODES), "filters": list(FILTERS), "laya_mode": self.config.laya.mode,
        }

    def documents(self) -> list[dict[str, Any]]:
        rows = self.database.query(
            "SELECT d.document_id, d.file_name, d.format, d.parse_status, d.error, d.coverage_json, d.warnings_json, "
            "d.document_family_id, d.ingested_at, "
            "(SELECT COUNT(*) FROM segments s WHERE s.document_id = d.document_id) AS segments, "
            "(SELECT COUNT(*) FROM segments s JOIN latest_predictions lp ON lp.segment_id = s.segment_id "
            " WHERE s.document_id = d.document_id AND lp.final_decision = 'CANDIDATE') AS candidates, "
            "(SELECT COUNT(*) FROM segments s JOIN resolved_labels rl ON rl.target_type = 'segment' "
            " AND rl.target_id = s.segment_id WHERE s.document_id = d.document_id) AS reviewed "
            "FROM documents d WHERE d.is_current = 1 ORDER BY d.file_name, d.ingested_at"
        )
        result = []
        for row in rows:
            item = dict(row)
            item["coverage"] = json.loads(item.pop("coverage_json"))
            item["warnings"] = json.loads(item.pop("warnings_json"))
            result.append(item)
        return result

    def document(self, document_id: str) -> dict[str, Any]:
        info = next((d for d in self.documents() if d["document_id"] == document_id), None)
        if info is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "문서를 찾을 수 없습니다.")
        locators = {row["paragraph_id"]: json.loads(row["source_locator_json"]) for row in self.database.query(
            "SELECT paragraph_id, source_locator_json FROM paragraphs WHERE document_id = ?", (document_id,))}
        groups: list[dict[str, Any]] = []
        unit_names = {"slide": "슬라이드", "page": "쪽"}
        unit_name = unit_names.get(info["coverage"].get("unit_type", ""), "")
        for item in fetch_segments(self.database, document_id=document_id):
            key = {"unit": item["unit"], "section_id": None if item["unit"] is not None else item["section_id"]}
            if not groups or groups[-1]["key"] != key:
                if item["unit"] is not None:
                    label = f"{item['unit']}쪽" if unit_name == "쪽" else f"{unit_name} {item['unit']}"
                else:
                    label = item["section_title"] or "본문"
                groups.append({"key": key, "label": label, "title": item["section_title"], "segments": []})
            parts = locators.get(item["paragraph_id"], {}).get("parts", [])
            groups[-1]["segments"].append(self._segment_view(item, info["format"], parts))
        for group in groups:
            group["reviewed"] = all(segment["human_label"] for segment in group["segments"])
            group["candidates"] = sum(segment["final_decision"] == "CANDIDATE" for segment in group["segments"])
        misses = [dict(row) for row in self.database.query(
            "SELECT id, unit, note, reviewer_id, created_at FROM unparsed_positives "
            "WHERE document_id = ? AND retracted_at IS NULL ORDER BY created_at", (document_id,))]
        return {"document": info, "groups": groups, "unparsed_positives": misses}

    @staticmethod
    def _segment_view(item: dict[str, Any], fmt: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
        text = item["original_text"]
        pieces = []
        cursor = 0
        for start, end in item["target_original_spans"]:
            if start > cursor:
                pieces.append({"text": text[cursor:start], "target": False})
            pieces.append({"text": text[start:end], "target": True})
            cursor = end
        if cursor < len(text):
            pieces.append({"text": text[cursor:], "target": False})
        return {
            "segment_id": item["segment_id"], "paragraph_id": item["paragraph_id"], "document_id": item["document_id"],
            "file_name": item["file_name"], "kind": item["kind"], "unit": item["unit"],
            "section_title": item["section_title"], "location": location_label(fmt, item["unit"], parts),
            "pieces": pieces, "model_text": item["normalized_text"],
            "score": item["stage1_score"], "final_decision": item["final_decision"], "threshold": item["threshold"],
            "classifier_version": item["classifier_version"], "prediction_id": item["prediction_id"],
            "human_label": item["human_label"], "label_implicit": bool(item["label_implicit"]),
            "adjudication_status": item["adjudication_status"], "quality_flags": item["quality_flags"],
            "rule_hints": item["rule_hints"], "queue_reason": item.get("queue_reason"),
        }

    def queue(self, queue_filter: str, document_id: str | None, offset: int, limit: int) -> dict[str, Any]:
        try:
            page = fetch_queue(self.database, queue_filter, document_id, limit=limit, offset=offset)
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        formats: dict[str, str] = {}
        items = []
        for item in page["items"]:
            fmt = formats.setdefault(item["document_id"], item["format"])
            row = self.database.query_one("SELECT source_locator_json FROM paragraphs WHERE paragraph_id = ?",
                                          (item["paragraph_id"],))
            parts = json.loads(row["source_locator_json"]).get("parts", []) if row else []
            items.append(self._segment_view(item, fmt, parts))
        return {"filter": page["filter"], "total": page["total"], "offset": page["offset"], "items": items}

    # ---- 쓰기 ----
    def feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return record_feedback(
                self.database, target_type=body.get("target_type", "segment"), target_id=str(body.get("target_id", "")),
                label=str(body.get("label", "")), reviewer_id=self.config.reviewer_id,
                guideline_version=self.config.review.label_guideline_version,
                reason_codes=list(body.get("reason_codes") or []), comment=body.get("comment"),
                prediction_id=body.get("prediction_id"),
            )
        except FeedbackError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def page_review(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return record_page_review(
                self.database, document_id=str(body.get("document_id", "")), unit=body.get("unit"),
                section_id=body.get("section_id"), yes_segment_ids=list(body.get("yes_segment_ids") or []),
                hold_segment_ids=list(body.get("hold_segment_ids") or []), reviewer_id=self.config.reviewer_id,
                guideline_version=self.config.review.label_guideline_version,
                predictions=dict(body.get("predictions") or {}),
            )
        except FeedbackError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc

    def unparsed(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            item_id = register_unparsed_positive(
                self.database, document_id=str(body.get("document_id", "")), unit=body.get("unit"),
                note=str(body.get("note", "")), reviewer_id=self.config.reviewer_id,
            )
        except FeedbackError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        return {"id": item_id}

    def history(self, target_type: str, target_id: str) -> list[dict[str, Any]]:
        return feedback_history(self.database, target_type, target_id)


def _static_bytes(name: str) -> bytes:
    return (resources.files("patent_marker") / "ui" / "static" / name).read_bytes()


def make_handler(app: ReviewApp, allowed_hosts: set[str]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PatentMarker"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # 요청 로그에 본문·쿼리를 남기지 않는다
            return

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _check_host(self) -> None:
            if (self.headers.get("Host") or "").lower() not in allowed_hosts:
                raise ApiError(HTTPStatus.FORBIDDEN, "허용되지 않은 Host입니다.")

        def _dispatch(self, method: str) -> None:
            try:
                self._check_host()
                parsed = urlparse(self.path)
                query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
                if method == "GET":
                    self._get(parsed.path, query)
                else:
                    self._post(parsed.path)
            except ApiError as exc:
                self._json({"error": exc.message}, exc.status)
            except Exception as exc:  # 내부 오류: 본문 내용 없이 종류만 알린다
                self._json({"error": f"내부 오류: {type(exc).__name__}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _get(self, path: str, query: dict[str, str]) -> None:
            if path in ("/", "/index.html"):
                page = _static_bytes("index.html").decode("utf-8").replace("{{PM_TOKEN}}", app.token)
                self._send(HTTPStatus.OK, page.encode("utf-8"), "text/html; charset=utf-8")
            elif path.startswith("/static/") and path[8:] in _STATIC:
                self._send(HTTPStatus.OK, _static_bytes(path[8:]), _STATIC[path[8:]])
            elif path == "/api/status":
                self._json(app.status())
            elif path == "/api/documents":
                self._json(app.documents())
            elif path == "/api/document":
                self._json(app.document(query.get("id", "")))
            elif path == "/api/queue":
                self._json(app.queue(query.get("filter", "candidates"), query.get("doc") or None,
                                     int(query.get("offset", "0")), min(200, int(query.get("limit", "50")))))
            elif path == "/api/history":
                self._json(app.history(query.get("target_type", "segment"), query.get("target_id", "")))
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "없는 경로입니다.")

        def _post(self, path: str) -> None:
            if not secrets.compare_digest(self.headers.get("X-PM-Token") or "", app.token):
                raise ApiError(HTTPStatus.FORBIDDEN, "토큰이 올바르지 않습니다. 화면을 새로 고치세요.")
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "요청이 너무 큽니다.")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 형식 오류") from exc
            if not isinstance(body, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "JSON 객체가 필요합니다.")
            if path == "/api/feedback":
                self._json(app.feedback(body))
            elif path == "/api/page-review":
                self._json(app.page_review(body))
            elif path == "/api/unparsed":
                self._json(app.unparsed(body))
            else:
                raise ApiError(HTTPStatus.NOT_FOUND, "없는 경로입니다.")

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return Handler


def create_server(database: Database, config: AppConfig, host: str | None = None,
                  port: int | None = None) -> tuple[ThreadingHTTPServer, ReviewApp]:
    host = host or config.ui.host
    port = config.ui.port if port is None else port
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("리뷰 화면은 127.0.0.1(루프백)에만 바인딩할 수 있습니다.")
    mimetypes.init()
    app = ReviewApp(database, config)
    server = ThreadingHTTPServer((host, port), make_handler(app, set()))
    actual_port = server.server_address[1]
    allowed = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}", f"[::1]:{actual_port}"}
    server.RequestHandlerClass = make_handler(app, allowed)
    return server, app
