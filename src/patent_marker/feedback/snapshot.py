"""검증된 라벨 snapshot과 그룹 분할 (스펙 10절, 12.1).

학습을 시작하기 전에 확정 YES/NO 라벨과 train/validation/test 분할을 변경 불가능한 파일로 고정한다.
분할 단위는 문서 계열(document_family_id)이며, 한 번 배정된 계열은 이후 snapshot에서도
같은 partition에 남는다(고정 test가 학습에 섞이지 않게 함).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..evaluation.splits import PARTITIONS, assign_partitions
from ..runtime import atomic_write_text, canonical_json, sha256_text, utc_now
from ..storage import Database

class SnapshotError(RuntimeError):
    pass


def trainable_rows(database: Database) -> list[dict[str, Any]]:
    """이진 학습에 쓸 수 있는 확정 segment 라벨. 미검토·HOLD·문단 수준 라벨·파싱 오류 건은 제외된다."""
    rows = database.query(
        "SELECT rl.target_id AS segment_id, rl.label, rl.implicit, rl.adjudication_status, "
        "rl.label_guideline_version, s.paragraph_id, s.document_id, s.normalized_text, s.text_hash, "
        "s.input_hash, s.context_segment_ids_json, s.token_count, d.document_family_id, d.format, p.kind "
        "FROM resolved_labels rl "
        "JOIN segments s ON s.segment_id = rl.target_id "
        "JOIN paragraphs p ON p.paragraph_id = s.paragraph_id "
        "JOIN documents d ON d.document_id = s.document_id "
        "WHERE rl.target_type = 'segment' AND rl.trainable = 1 AND rl.label IN ('YES', 'NO') "
        "ORDER BY s.document_id, s.seq"
    )
    result = []
    for row in rows:
        item = dict(row)
        item["context_segment_ids"] = json.loads(item.pop("context_segment_ids_json"))
        item["implicit"] = bool(item["implicit"])
        result.append(item)
    return result


def create_snapshot(database: Database, config: AppConfig, name: str, output: Path | None = None) -> dict[str, Any]:
    """라벨 snapshot(JSONL)과 분할 manifest(JSON)를 만들고 등록한다."""
    if database.query_one("SELECT 1 FROM label_snapshots WHERE snapshot_id = ?", (name,)):
        raise SnapshotError(f"이미 있는 snapshot 이름입니다: {name}. snapshot은 덮어쓰지 않습니다.")
    rows = trainable_rows(database)
    if not rows:
        raise SnapshotError("확정 YES/NO 라벨이 없습니다. 리뷰 화면에서 먼저 라벨을 수집하세요.")
    path = Path(output) if output else config.data_dir / "snapshots" / f"{name}.jsonl"
    if path.exists():
        raise SnapshotError(f"이미 있는 파일입니다: {path}")
    split_tag = name.split("-", 1)[1] if "-" in name else name

    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        stats = groups.setdefault(row["document_family_id"], {"n": 0, "pos": 0})
        stats["n"] += 1
        stats["pos"] += row["label"] == "YES"
    assignment = assign_partitions(database, groups, config.evaluation.split_ratios,
                                   config.classifier.random_seed, split_tag)
    lines = []
    for row in rows:
        row["partition"] = assignment[row["document_family_id"]]
        lines.append(canonical_json(row))
    body = "\n".join(lines) + "\n"
    label_hash = sha256_text(body)

    counts = {name_: {"n": 0, "pos": 0, "groups": 0} for name_ in PARTITIONS}
    for group, partition in assignment.items():
        counts[partition]["groups"] += 1
        counts[partition]["n"] += groups[group]["n"]
        counts[partition]["pos"] += groups[group]["pos"]
    manifest = {
        "schema_version": 1, "snapshot_id": name, "split_tag": split_tag, "created_at": utc_now(),
        "label_snapshot_hash": label_hash, "group_unit": "document_family_id",
        "ratios": dict(zip(PARTITIONS, config.evaluation.split_ratios)),
        "random_seed": config.classifier.random_seed,
        "assignment": dict(sorted(assignment.items())), "counts": counts,
        "label_guideline_version": config.review.label_guideline_version,
    }
    manifest_body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    split_hash = sha256_text(canonical_json(manifest["assignment"]))
    atomic_write_text(path, body)
    manifest_path = path.with_suffix(".split.json")
    atomic_write_text(manifest_path, manifest_body)
    stats = {"labels": len(rows), "yes": sum(r["label"] == "YES" for r in rows),
             "no": sum(r["label"] == "NO" for r in rows), "implicit": sum(r["implicit"] for r in rows),
             "families": len(groups), "partitions": counts}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO label_snapshots (snapshot_id, path, label_snapshot_hash, split_manifest_hash, created_at, "
            "stats_json) VALUES (?, ?, ?, ?, ?, ?)",
            (name, str(path), label_hash, split_hash, utc_now(), json.dumps(stats, ensure_ascii=False)),
        )
    return {"snapshot_id": name, "path": str(path), "manifest_path": str(manifest_path), "split_tag": split_tag,
            "label_snapshot_hash": label_hash, "split_manifest_hash": split_hash, "stats": stats}


def load_snapshot(database: Database, path: Path) -> dict[str, Any]:
    """snapshot 파일을 읽고 등록된 해시와 일치하는지 확인한다(변경 불가)."""
    path = Path(path)
    if not path.is_file():
        raise SnapshotError(f"snapshot 파일이 없습니다: {path}")
    body = path.read_text(encoding="utf-8")
    label_hash = sha256_text(body)
    record = database.query_one("SELECT * FROM label_snapshots WHERE label_snapshot_hash = ?", (label_hash,))
    if record is None:
        raise SnapshotError("등록되지 않았거나 내용이 바뀐 snapshot입니다. snapshot 명령으로 새로 만드세요.")
    rows = [json.loads(line) for line in body.splitlines() if line.strip()]
    manifest_path = path.with_suffix(".split.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if sha256_text(canonical_json(manifest["assignment"])) != record["split_manifest_hash"]:
        raise SnapshotError("분할 manifest가 등록된 내용과 다릅니다.")
    return {"snapshot_id": record["snapshot_id"], "rows": rows, "manifest": manifest,
            "label_snapshot_hash": label_hash, "split_manifest_hash": record["split_manifest_hash"],
            "split_tag": manifest["split_tag"]}


def find_snapshot(database: Database, split_tag: str) -> Path:
    """split tag(예: 'v1')로 가장 최근 snapshot 경로를 찾는다."""
    rows = database.query("SELECT snapshot_id, path FROM label_snapshots ORDER BY created_at DESC")
    for row in rows:
        tag = row["snapshot_id"].split("-", 1)[1] if "-" in row["snapshot_id"] else row["snapshot_id"]
        if tag == split_tag or row["snapshot_id"] == split_tag:
            return Path(row["path"])
    raise SnapshotError(f"split '{split_tag}'에 해당하는 snapshot이 없습니다.")
