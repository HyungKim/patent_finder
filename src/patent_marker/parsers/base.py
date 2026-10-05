"""파서 공통 자료구조. 원문 텍스트와 위치를 함께 보존한다 (스펙 4절)."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_EXTENSIONS = {".txt": "txt", ".md": "md", ".docx": "docx", ".pptx": "pptx", ".pdf": "pdf"}

UNSUPPORTED_REASONS = {
    ".ppt": "구형 PPT 형식은 지원하지 않습니다. PPTX로 저장한 뒤 다시 시도하세요.",
    ".doc": "구형 DOC 형식은 지원하지 않습니다. DOCX로 저장한 뒤 다시 시도하세요.",
    ".hwp": "HWP 형식은 지원하지 않습니다. PDF 또는 DOCX로 변환한 뒤 다시 시도하세요.",
    ".hwpx": "HWPX 형식은 지원하지 않습니다. PDF 또는 DOCX로 변환한 뒤 다시 시도하세요.",
    ".pptm": "매크로 포함 문서(PPTM)는 지원하지 않습니다.",
    ".docm": "매크로 포함 문서(DOCM)는 지원하지 않습니다.",
    ".png": "이미지 파일은 지원하지 않습니다 (로컬 OCR은 후속 단계).",
    ".jpg": "이미지 파일은 지원하지 않습니다 (로컬 OCR은 후속 단계).",
    ".jpeg": "이미지 파일은 지원하지 않습니다 (로컬 OCR은 후속 단계).",
    ".tif": "이미지 파일은 지원하지 않습니다 (로컬 OCR은 후속 단계).",
    ".tiff": "이미지 파일은 지원하지 않습니다 (로컬 OCR은 후속 단계).",
}

# 문단 머리의 목록 표식 (글머리 기호 또는 번호)
# 전용 글머리 기호는 뒤에 공백이 없어도 표식으로 본다 (전각 글꼴 PDF에서는 기호와 본문이 붙어 추출된다).
# 본문에도 쓰이는 기호(-, *, + 등)는 뒤에 공백이 있을 때만 표식으로 본다.
STRONG_BULLETS = "•∙‣⁃◦○●◎◇◆□■▪▫▶▷►▻➢➤➔※✓✔"
WEAK_BULLETS = "·-–—*+→"
BULLET_CHARS = STRONG_BULLETS + WEAK_BULLETS
BULLET_RE = re.compile(rf"^\s*(?:([{re.escape(STRONG_BULLETS)}])\s*|([{re.escape(WEAK_BULLETS)}])\s+)")
# "- 3 -" 같은 쪽 번호는 목록 표식이 아니다.
PAGE_NUMBER_RE = re.compile(r"^\s*[-–—]\s*\d{1,4}\s*[-–—]\s*$")
NUMBER_RE = re.compile(r"^\s*(\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[가-힣][.)]|[①-⑳]|[ⅰ-ⅹⅠ-Ⅹ][.)])\s+")


@dataclass
class Block:
    """원문에서 추출한 하나의 원시 단위 (문단, 목록 항목, 표 행 등)."""

    kind: str  # title | heading | paragraph | list_item | table_row | notes | header_footer | footnote | diagram_text
    text: str
    locator: dict[str, Any]
    unit: int | None = None  # 페이지/슬라이드 번호 (1부터)
    container: str | None = None  # 같은 텍스트 상자/목록을 나타내는 키 (불릿 묶음 판단용)
    level: int = 0
    section_key: str | None = None
    section_title: str | None = None
    flags: list[str] = field(default_factory=list)
    # 표 행: text 안에서 각 셀의 구간과 열 제목. [{"col", "start", "end", "header"}]
    cells: list[dict[str, Any]] | None = None
    # False면 text 안의 줄바꿈은 시각적 줄바꿈(soft wrap)이며 문장 경계가 아니다.
    hard_newlines: bool = True


@dataclass
class ParseResult:
    format: str
    status: str  # ok | partial | unsupported | failed
    blocks: list[Block] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def warn(self, code: str, message: str, unit: int | None = None) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if unit is not None:
            item["unit"] = unit
        self.warnings.append(item)


class UnsupportedDocument(Exception):
    """지원 범위 밖의 문서. '후보 없음'이 아니라 오류 목록에 남겨야 한다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def check_zip_limits(path: Path, max_uncompressed_mb: int) -> None:
    """OOXML(zip) 문서의 압축 해제 용량을 확인한다. 암호화 문서는 zip이 아니다."""
    if not zipfile.is_zipfile(path):
        raise UnsupportedDocument(
            "not_ooxml", "OOXML(zip) 문서가 아닙니다. 암호화되었거나 손상되었거나 구형 형식일 수 있습니다."
        )
    limit = max_uncompressed_mb * (1 << 20)
    total = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            total += info.file_size
            if total > limit:
                raise UnsupportedDocument(
                    "uncompressed_limit",
                    f"압축 해제 용량이 제한({max_uncompressed_mb}MB)을 넘습니다. limits.max_uncompressed_mb를 확인하세요.",
                )


def strip_list_marker(text: str) -> tuple[str, int, str | None]:
    """문단 머리의 목록 표식을 찾는다. (표식 종류, 본문 시작 offset)을 돌려준다.

    글머리 기호는 본문에서 제외하고, 번호 표식("1.", "가.")은 의미가 있으므로 남긴다.
    반환: (kind, body_start, marker) — kind는 'bullet' | 'number' | ''.
    """
    if PAGE_NUMBER_RE.match(text):
        return "", 0, None
    match = BULLET_RE.match(text)
    if match and match.end() < len(text):
        return "bullet", match.end(), match.group(1) or match.group(2)
    match = NUMBER_RE.match(text)
    if match:
        return "number", match.start(1), match.group(1)
    return "", 0, None


def finalize_status(result: ParseResult) -> ParseResult:
    """coverage와 오류 정보로 parse_status를 정한다."""
    if result.status in ("unsupported", "failed"):
        return result
    cov = result.coverage
    problems = (
        cov.get("units_failed", 0)
        or cov.get("units_ocr_needed", 0)
        or cov.get("rotated_chars_skipped", 0)
    )
    result.status = "partial" if problems else "ok"
    return result
