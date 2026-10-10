#!/usr/bin/env python3
"""실험용 2단계 모델(Laya-multilingual)을 고정 revision으로 받아 manifest를 만든다.

- 인터넷이 허용된 준비 환경에서만 실행한다. 운영 코드(src/patent_marker)는 이 파일을 import하지 않으며
  어떤 다운로드도 하지 않는다.
- 표준 라이브러리만 사용한다. 받는 방식과 해시 검증은 tools/fetch_model.py와 같다.
- Laya는 기본 설치에 들어 있지 않은 실험 기능이다 (docs/LAYA_EXPERIMENT.md).

사용법:
    python tools/fetch_laya_model.py --dest models/laya-multilingual
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_model import _download, _get_json, _git_blob_sha1  # noqa: E402

REPO_ID = "convaiinnovations/laya-multilingual"
REVISION = "1720e3e3357cfe1e281542e223f8273b0890ca34"  # 2026-10-05 확인한 main의 commit
LICENSE = "apache-2.0"
# 저장소 경로 -> 로컬 경로. Laya SDK가 읽는 폴더 구조(encoder/, tokenizer/)를 그대로 둔다.
FILES = {
    "config.json": "config.json",
    "rl_agent_config.json": "rl_agent_config.json",
    "encoder/config.json": "encoder/config.json",
    "model.safetensors": "model.safetensors",
    "tokenizer/tokenizer.json": "tokenizer/tokenizer.json",
    "tokenizer/tokenizer_config.json": "tokenizer/tokenizer_config.json",
    "README.md": "MODEL_CARD.md",
}


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default="models/laya-multilingual")
    args = parser.parse_args(argv)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] Hub 메타데이터 확인: {REPO_ID}@{REVISION[:12]}")
    info = _get_json(f"https://huggingface.co/api/models/{REPO_ID}/revision/{REVISION}?blobs=true")
    if info.get("sha") != REVISION:
        print(f"오류: revision 불일치 ({info.get('sha')})", file=sys.stderr)
        return 1
    siblings = {item["rfilename"]: item for item in info.get("siblings", [])}

    print("[2/3] 파일 다운로드 및 해시 검증")
    manifest_files = {}
    for remote, local in FILES.items():
        meta = siblings.get(remote)
        if meta is None:
            print(f"오류: 저장소에 {remote} 없음", file=sys.stderr)
            return 1
        target = dest / local
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"  {remote} -> {target}")
        sha256, size = _download(f"https://huggingface.co/{REPO_ID}/resolve/{REVISION}/{remote}", target)
        expected_size = meta.get("size")
        if expected_size is not None and expected_size != size:
            print(f"오류: 크기 불일치 {remote}: {size} != {expected_size}", file=sys.stderr)
            return 1
        lfs = meta.get("lfs") or {}
        if lfs.get("sha256"):
            verified_by = "hub-lfs-sha256"
            ok = lfs["sha256"] == sha256
        else:
            verified_by = "hub-git-blob-sha1"
            ok = meta.get("blobId") == _git_blob_sha1(target)
        if not ok:
            print(f"오류: 해시 불일치 {remote}", file=sys.stderr)
            return 1
        manifest_files[local] = {"sha256": sha256, "size": size, "source_path": remote, "verified_by": verified_by}

    print("[3/3] manifest 기록")
    manifest = {
        "schema_version": 1,
        "model_id": REPO_ID,
        "revision": REVISION,
        "source": f"https://huggingface.co/{REPO_ID}/tree/{REVISION}",
        "license": LICENSE,
        "runtime": "laya (PyTorch, CPU)",
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": manifest_files,
    }
    manifest_path = dest / "manifest.json"
    tmp = manifest_path.with_suffix(".json.part")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)
    print(f"완료: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
