"""형식별 파서 진입점. 미지원·실패 문서는 '후보 없음'이 아니라 상태와 사유로 돌려준다."""
from __future__ import annotations

from pathlib import Path

from ..config import AppConfig
from .base import SUPPORTED_EXTENSIONS, UNSUPPORTED_REASONS, Block, ParseResult, UnsupportedDocument

__all__ = ["Block", "ParseResult", "parse_document", "detect_format", "SUPPORTED_EXTENSIONS"]


def detect_format(path: Path) -> str | None:
    return SUPPORTED_EXTENSIONS.get(path.suffix.lower())


def parse_document(path: Path, config: AppConfig, encoding: str | None = None) -> ParseResult:
    """문서를 파싱한다. 원본 파일은 읽기만 한다. 문서 안의 매크로·링크·명령은 실행하지 않는다."""
    path = Path(path)
    suffix = path.suffix.lower()
    fmt = SUPPORTED_EXTENSIONS.get(suffix)
    if fmt is None:
        reason = UNSUPPORTED_REASONS.get(suffix, f"지원하지 않는 파일 형식입니다 ({suffix or '확장자 없음'}).")
        return ParseResult(format=suffix.lstrip(".") or "unknown", status="unsupported", error=reason)
    size_mb = path.stat().st_size / (1 << 20)
    if size_mb > config.limits.max_file_mb:
        return ParseResult(
            format=fmt, status="unsupported",
            error=f"파일 크기({size_mb:.0f}MB)가 제한({config.limits.max_file_mb}MB)을 넘습니다. limits.max_file_mb를 확인하세요.",
        )
    try:
        if fmt in ("txt", "md"):
            from .text import parse_text

            return parse_text(path, fmt, encoding)
        if fmt == "docx":
            from .docx import parse_docx

            return parse_docx(path, config)
        if fmt == "pptx":
            from .pptx import parse_pptx

            return parse_pptx(path, config)
        from .pdf import parse_pdf

        return parse_pdf(path, config)
    except UnsupportedDocument as exc:
        return ParseResult(format=fmt, status="unsupported", error=exc.message)
    except Exception as exc:  # 손상 파일 등: 본문을 로그에 남기지 않고 오류 종류만 기록
        return ParseResult(format=fmt, status="failed", error=f"파싱 실패: {type(exc).__name__}: {exc}")
