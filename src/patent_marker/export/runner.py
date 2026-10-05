"""한 run의 결과물을 출력 디렉터리에 만든다. 원본이 있는 디렉터리에는 쓰지 않는다."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..runtime import atomic_write_json
from ..storage import Database
from .annotations import annotate_documents
from .data import ExportError, load_run
from .html import export_html
from .jsonl import export_jsonl

FORMATS = ("html", "jsonl", "annotated")


def export_run(database: Database, config: AppConfig, run_id: str, output_dir: Path,
               formats: list[str] | None = None) -> dict[str, Any]:
    formats = list(formats or config.export.formats)
    unknown = sorted(set(formats) - set(FORMATS))
    if unknown:
        raise ExportError(f"지원하지 않는 내보내기 형식: {', '.join(unknown)} (지원: {', '.join(FORMATS)})")
    run = load_run(database, run_id)
    output_dir = Path(output_dir).resolve()
    for document in run["documents"]:
        local = document.get("local_path")
        if local and Path(local).resolve().parent == output_dir:
            raise ExportError("출력 디렉터리가 원본 문서의 디렉터리와 같습니다. 다른 경로를 지정하세요.")
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"run_id": run_id, "status": run["status"], "model_stage": run["model_stage"],
                              "output_dir": str(output_dir), "files": {}}
    if "jsonl" in formats:
        path = output_dir / "results.jsonl"
        result["files"]["jsonl"] = {"path": str(path), "rows": export_jsonl(run, path, config.laya.mode)}
    if "html" in formats:
        path = output_dir / "report.html"
        result["files"]["html"] = {"path": str(path), **export_html(run, path)}
    if "annotated" in formats:
        result["annotated"] = annotate_documents(run, output_dir / "marked", config.export.pptx_summary_slide)
    result["problems"] = [
        {"file": Path(document["input_path"]).name, "status": document["run_status"],
         "reason": document["run_error"] or document["error"]}
        for document in run["documents"] if document["run_status"] in ("unsupported", "failed")
    ]
    atomic_write_json(output_dir / "export_summary.json", result)
    return result
