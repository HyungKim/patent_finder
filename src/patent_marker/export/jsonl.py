"""JSONL 내보내기 (스펙 9.3). segment 한 줄. 임베딩 배열 대신 재현 가능한 참조를 넣는다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..runtime import atomic_write_text
from ..versions import JSONL_SCHEMA_VERSION
from .data import target_text


def export_jsonl(run: dict[str, Any], path: Path, laya_mode: str = "off") -> int:
    lines = []
    policy = run.get("policy") or {}
    for document in run["documents"]:
        for paragraph in document["paragraphs"]:
            for segment in paragraph["segments"]:
                embedding_ref = None
                if segment.get("embedding_file"):
                    embedding_ref = f"{segment['embedding_file']}#row={segment['embedding_row']}"
                feedback = segment["feedback"]
                lines.append(json.dumps({
                    "schema_version": JSONL_SCHEMA_VERSION,
                    "run_id": run["run_id"],
                    "document_id": document["document_id"],
                    "file_name": document["file_name"],
                    "paragraph_id": paragraph["paragraph_id"],
                    "segment_id": segment["segment_id"],
                    "location": paragraph["location"],
                    "source_locator": paragraph["locator"],
                    "target_original_spans": [list(span) for span in segment["spans"]],
                    "original_text": target_text(paragraph, segment),
                    "input_hash": "sha256:" + segment["input_hash"],
                    "embedding_ref": embedding_ref,
                    "prediction_id": segment["prediction_id"],
                    "encoder_revision": segment.get("encoder_revision"),
                    "classifier_version": segment["classifier_version"],
                    "model_stage": run["model_stage"],
                    "policy_version": segment["prediction_policy"],
                    "policy_status": policy.get("status"),
                    "stage1_score": segment["stage1_score"],
                    "stage1_threshold": segment["prediction_threshold"],
                    "stage1_decision": segment["stage1_decision"],
                    "laya": {"mode": laya_mode},
                    "final_decision": segment["final_decision"],
                    "rule_hints": segment["rule_hints"],
                    "quality_flags": sorted(set(segment["quality_flags"] + paragraph["quality_flags"])),
                    "feedback": None if feedback is None else {
                        "label": feedback["label"], "reason_codes": feedback["reason_codes"],
                        "reviewer_id": ",".join(feedback["reviewer_ids"]),
                        "adjudication_status": feedback["adjudication_status"], "implicit": feedback["implicit"],
                    },
                }, ensure_ascii=False))
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)
