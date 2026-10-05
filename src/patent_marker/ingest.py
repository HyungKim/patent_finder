"""문서 수집: 파싱 → 논리 문단 → segment → DB 저장 (스펙 4·5·9절).

- 원본은 읽기만 하며 처리 전후 해시가 같은지 확인한다.
- 같은 파일을 같은 처리 버전으로 다시 넣어도 중복 행이 생기지 않는다(결정적 ID).
- 미지원·실패 문서도 상태와 사유를 남긴다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parsers import parse_document
from .runtime import canonical_json, sha256_file, sha256_text, utc_now
from .segmentation.context import assign_context, context_summary, input_hash
from .segmentation.normalization import normalize_text
from .segmentation.paragraphs import build_paragraphs
from .segmentation.segmenter import SegmentationError, segment_paragraph
from .services import Services
from .versions import PARSER_VERSION, PREPROCESS_VERSION, SEGMENTATION_VERSION

_FAMILY_MIN_SHARED = 3
_FAMILY_MIN_CONTAINMENT = 0.5
_FAMILY_MIN_CHARS = 20
_CARRIED_FLAGS = ("hidden_slide", "speaker_notes", "table_header", "title_inferred", "smartart",
                  "chart_title", "textbox")


@dataclass
class IngestResult:
    path: Path
    document_id: str | None
    status: str  # ok | partial | unsupported | failed
    reused: bool = False
    error: str | None = None
    paragraphs: int = 0
    segments: int = 0
    existing_marks: dict[str, int] | None = None


def discover_files(input_path: Path) -> list[Path]:
    """입력 경로의 파일 목록. 미지원 형식도 포함해 오류 목록에 남길 수 있게 한다."""
    input_path = Path(input_path)
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"입력 경로가 없습니다: {input_path}")
    files = []
    for path in sorted(input_path.rglob("*")):
        if path.is_file() and not path.name.startswith((".", "~$")):
            files.append(path)
    return files


def pipeline_hash(services: Services) -> str:
    return sha256_text(canonical_json({
        "parser": PARSER_VERSION, "preprocess": PREPROCESS_VERSION, "segmentation": SEGMENTATION_VERSION,
        "options": services.config.parse_options_hash(),
        "tokenizer": getattr(services.tokenizer, "identity", type(services.tokenizer).__name__),
    }))[:32]


def _assign_family(connection: Any, content_sha256: str, text_hashes: set[str]) -> str:
    """같은 내용이거나 문단이 절반 이상 겹치는 기존 문서가 있으면 그 계열에 넣는다."""
    same = connection.execute(
        "SELECT document_family_id FROM documents WHERE content_sha256 = ? LIMIT 1", (content_sha256,)
    ).fetchone()
    if same:
        return same["document_family_id"]
    shared: dict[str, set[str]] = {}
    hashes = sorted(text_hashes)
    for start in range(0, len(hashes), 500):
        chunk = hashes[start: start + 500]
        rows = connection.execute(
            f"SELECT document_id, text_hash FROM paragraphs WHERE text_hash IN ({','.join('?' * len(chunk))})", chunk
        ).fetchall()
        for row in rows:
            shared.setdefault(row["document_id"], set()).add(row["text_hash"])
    best: tuple[float, str] | None = None
    for document_id, common in shared.items():
        if len(common) < _FAMILY_MIN_SHARED:
            continue
        other = connection.execute(
            "SELECT COUNT(DISTINCT text_hash) AS n FROM paragraphs WHERE document_id = ? AND LENGTH(original_text) >= ?",
            (document_id, _FAMILY_MIN_CHARS),
        ).fetchone()["n"]
        containment = len(common) / max(1, min(len(text_hashes), other or len(common)))
        if containment >= _FAMILY_MIN_CONTAINMENT and (best is None or containment > best[0]):
            family = connection.execute(
                "SELECT document_family_id FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()["document_family_id"]
            best = (containment, family)
    return best[1] if best else f"fam-{content_sha256[:12]}"


def ingest_file(services: Services, path: Path, encoding: str | None = None) -> IngestResult:
    path = Path(path)
    config, database = services.config, services.database
    try:
        content_sha256 = sha256_file(path)
    except OSError as exc:
        return IngestResult(path, None, "failed", error=f"파일을 읽을 수 없습니다: {exc}")
    pipe = pipeline_hash(services)
    existing = database.query_one(
        "SELECT document_id, parse_status, error, coverage_json FROM documents "
        "WHERE content_sha256 = ? AND pipeline_hash = ?",
        (content_sha256, pipe),
    )
    if existing:
        counts = database.query_one(
            "SELECT (SELECT COUNT(*) FROM paragraphs WHERE document_id = :d) AS p, "
            "(SELECT COUNT(*) FROM segments WHERE document_id = :d) AS s", {"d": existing["document_id"]},
        )
        with database.transaction() as connection:
            connection.execute("UPDATE documents SET is_current = 0 WHERE content_sha256 = ? AND document_id != ?",
                               (content_sha256, existing["document_id"]))
            connection.execute("UPDATE documents SET is_current = 1, local_path = ?, file_name = ? WHERE document_id = ?",
                               (str(path.resolve()), path.name, existing["document_id"]))
        return IngestResult(path, existing["document_id"], existing["parse_status"], reused=True,
                            error=existing["error"], paragraphs=counts["p"], segments=counts["s"],
                            existing_marks=json.loads(existing["coverage_json"]).get("existing_marks"))

    result = parse_document(path, config, encoding)
    if sha256_file(path) != content_sha256:
        return IngestResult(path, None, "failed", error="처리 중에 원본 파일이 바뀌었습니다. 다시 실행하세요.")

    document_id = "doc-" + sha256_text(content_sha256 + pipe)[:16]
    paragraph_rows: list[tuple[Any, ...]] = []
    segment_rows: list[dict[str, Any]] = []
    section_keys: list[str | None] = []
    headings: list[bool] = []
    family_hashes: set[str] = set()
    segmentation_errors = 0

    if result.status in ("ok", "partial"):
        paragraphs = build_paragraphs(result.blocks, services.tokenizer, config.segmentation)
        for order, paragraph in enumerate(paragraphs):
            paragraph_id = f"{document_id}-p{order:04d}"
            normalized = normalize_text(paragraph.original_text)
            text_hash = sha256_text(normalized)
            if len(normalized) >= _FAMILY_MIN_CHARS:
                family_hashes.add(text_hash)
            flags = list(paragraph.flags)
            try:
                drafts = segment_paragraph(paragraph, services.tokenizer, config.segmentation)
            except SegmentationError as exc:
                drafts = []
                flags.append("segmentation_error")
                segmentation_errors += 1
                result.warn("segmentation_error", f"문단 {order}을(를) 분할하지 못했습니다: {exc}", unit=paragraph.unit)
            locator: dict[str, Any] = {"format": result.format, "parts": paragraph.parts}
            if paragraph.cells:
                locator["cells"] = [
                    {key: cell[key] for key in ("col", "start", "end", "header") if key in cell}
                    for cell in paragraph.cells
                ]
            paragraph_rows.append((
                paragraph_id, document_id, order, paragraph.kind, paragraph.unit, paragraph.original_text,
                json.dumps(locator, ensure_ascii=False), paragraph.section_key, paragraph.section_title,
                text_hash, json.dumps(flags),
            ))
            carried = [flag for flag in flags if flag in _CARRIED_FLAGS]
            for index, draft in enumerate(drafts):
                segment_rows.append({
                    "segment_id": f"{paragraph_id}-s{index:02d}", "paragraph_id": paragraph_id,
                    "segment_index": index, "spans": draft.spans, "text": draft.text,
                    "token_count": draft.token_count, "flags": sorted(set(draft.flags + carried)),
                })
                section_keys.append(paragraph.section_key)
                headings.append(paragraph.is_heading)
        if segmentation_errors:
            result.coverage["segmentation_errors"] = segmentation_errors
            result.status = "partial"

    refs = assign_context(section_keys, headings)
    ids = [row["segment_id"] for row in segment_rows]
    texts = [row["text"] for row in segment_rows]

    def text_at(position: int | None) -> str | None:
        return texts[position] if position is not None else None

    with database.transaction() as connection:
        family = _assign_family(connection, content_sha256, family_hashes)
        connection.execute("UPDATE documents SET is_current = 0 WHERE content_sha256 = ?", (content_sha256,))
        connection.execute(
            "INSERT INTO documents (document_id, content_sha256, local_path, file_name, format, size_bytes, "
            "document_family_id, ingested_at, parse_status, parser_version, preprocess_version, "
            "segmentation_version, pipeline_hash, coverage_json, warnings_json, error, is_current) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (document_id, content_sha256, str(path.resolve()), path.name, result.format, path.stat().st_size,
             family, utc_now(), result.status, PARSER_VERSION, PREPROCESS_VERSION, SEGMENTATION_VERSION, pipe,
             json.dumps(result.coverage, ensure_ascii=False), json.dumps(result.warnings, ensure_ascii=False),
             result.error),
        )
        connection.executemany(
            "INSERT INTO paragraphs (paragraph_id, document_id, order_index, kind, unit, original_text, "
            "source_locator_json, section_id, section_title, text_hash, quality_flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", paragraph_rows,
        )
        connection.executemany(
            "INSERT INTO segments (segment_id, paragraph_id, document_id, seq, segment_index, "
            "target_original_spans_json, normalized_text, text_hash, context_segment_ids_json, input_hash, "
            "token_count, preprocess_version, segmentation_version, quality_flags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (row["segment_id"], row["paragraph_id"], document_id, seq, row["segment_index"],
                 json.dumps(row["spans"]), row["text"], sha256_text(row["text"]),
                 json.dumps(context_summary(refs[seq], ids)),
                 input_hash(row["text"], text_at(refs[seq].title), text_at(refs[seq].prev), text_at(refs[seq].next)),
                 row["token_count"], PREPROCESS_VERSION, SEGMENTATION_VERSION, json.dumps(row["flags"]))
                for seq, row in enumerate(segment_rows)
            ],
        )
    return IngestResult(path, document_id, result.status, error=result.error,
                        paragraphs=len(paragraph_rows), segments=len(segment_rows),
                        existing_marks=result.coverage.get("existing_marks"))
