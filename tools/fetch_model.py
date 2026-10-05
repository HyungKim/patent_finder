#!/usr/bin/env python3
"""반입 준비 전용 도구: 고정 revision의 E5 ONNX 모델 파일을 받아 manifest를 만든다.

- 인터넷이 허용된 준비 환경에서만 실행한다. 운영 PC에서는 실행하지 않는다.
- 운영 코드(src/patent_marker)는 이 파일을 import하지 않으며 어떤 다운로드도 하지 않는다.
- 표준 라이브러리만 사용한다.

사용법:
    python tools/fetch_model.py --dest models/multilingual-e5-small
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ID = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"  # 2026-10-05 확인한 main의 commit
LICENSE = "mit"
# 저장소 경로 -> 로컬 파일명
FILES = {
    "onnx/model.onnx": "model.onnx",
    "onnx/tokenizer.json": "tokenizer.json",
    "onnx/config.json": "config.json",
    "README.md": "MODEL_CARD.md",
}
EMBEDDING_INFO = {"dimension": 384, "max_tokens": 512, "pooling": "mean", "prefix": "query: "}
CHUNK = 1 << 20


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def _git_blob_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    digest.update(f"blob {path.stat().st_size}\0".encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> tuple[str, int]:
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        for chunk in iter(lambda: response.read(CHUNK), b""):
            handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
            if total and size % (50 * CHUNK) < CHUNK:
                print(f"    {size / 1e6:7.1f} / {total / 1e6:.1f} MB", flush=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, dest)
    return digest.hexdigest(), size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default="models/multilingual-e5-small")
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
        manifest_files[local] = {
            "sha256": sha256,
            "size": size,
            "source_path": remote,
            "verified_by": verified_by,
        }

    print("[3/3] manifest 기록")
    manifest = {
        "schema_version": 1,
        "model_id": REPO_ID,
        "revision": REVISION,
        "source": f"https://huggingface.co/{REPO_ID}/tree/{REVISION}",
        "license": LICENSE,
        "runtime": "onnxruntime",
        "retrieved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "embedding": EMBEDDING_INFO,
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
