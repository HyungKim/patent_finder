#!/usr/bin/env python3
"""반입 준비 전용 도구: 대상 PC용 설치 패키지(wheel)를 받고 반입 목록(manifest)을 만든다.

- 인터넷이 허용된 준비 환경에서만 실행한다. 운영 PC에서는 실행하지 않는다.
- 운영 코드(src/patent_marker)는 이 파일을 import하지 않는다.
- 모델 파일은 tools/fetch_model.py로 먼저 받아 둔다.

사용법 (기본: Windows x86-64, Python 3.11):
    python tools/prepare_offline_bundle.py
    python tools/prepare_offline_bundle.py --python-version 3.11 3.12 3.13
    python tools/prepare_offline_bundle.py --platform manylinux_2_28_x86_64 --python-version 3.11

pip download를 쓰므로 pip이 들어 있는 Python으로 실행한다 (uv venv로 만든 환경에는 pip이 없다).

주의: 이 도구의 다운로드 단계는 대상 Windows PC에서의 설치까지 검증된 것이 아니다.
반입 후 운영 PC에서 docs/OFFLINE_INSTALL.md의 설치·점검 절차를 반드시 수행한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--platform", default="win_amd64", help="pip 플랫폼 태그 (기본 win_amd64)")
    parser.add_argument("--python-version", nargs="+", default=["3.11"],
                        help="대상 Python 버전. 여러 개를 주면 버전마다 받아 한 폴더에 모은다 (예: 3.11 3.12 3.13)")
    parser.add_argument("--lock", default=str(ROOT / "requirements.lock"))
    parser.add_argument("--dest", default=str(ROOT / "vendor" / "wheels"))
    parser.add_argument("--model-dir", default=str(ROOT / "models" / "multilingual-e5-small"))
    args = parser.parse_args(argv)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for version in args.python_version:
        abi = "cp" + version.replace(".", "")
        command = [
            sys.executable, "-m", "pip", "download", "--dest", str(dest), "--only-binary=:all:", "--no-deps",
            "--platform", args.platform, "--python-version", version, "--implementation", "cp",
            "--abi", abi, "--abi", "abi3", "--abi", "none", "--require-hashes", "-r", args.lock,
        ]
        print(f"[1/3] wheel 내려받기 (Python {version}):", " ".join(command))
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            print("오류: pip download 실패. lock 파일이 대상 플랫폼·Python 버전과 맞는지, 이 Python에 pip이 있는지 "
                  "확인하세요.", file=sys.stderr)
            return result.returncode

    print("[2/3] 모델 manifest 확인")
    model_dir = Path(args.model_dir)
    manifest_path = model_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"오류: 모델 manifest가 없습니다: {manifest_path}. 먼저 tools/fetch_model.py를 실행하세요.", file=sys.stderr)
        return 1
    model = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, meta in model["files"].items():
        if sha256(model_dir / name) != meta["sha256"]:
            print(f"오류: 모델 파일 해시 불일치: {name}", file=sys.stderr)
            return 1

    print("[3/3] 반입 목록 작성")
    wheels = [{"file": path.name, "size": path.stat().st_size, "sha256": sha256(path)}
              for path in sorted(dest.glob("*.whl"))]
    bundle = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"platform": args.platform, "pythons": list(args.python_version)},
        "lock_file": {"path": Path(args.lock).name, "sha256": sha256(Path(args.lock))},
        "wheels": wheels,
        "model": {"model_id": model["model_id"], "revision": model["revision"], "license": model["license"],
                  "source": model["source"], "files": model["files"]},
        "install": "python -m pip install --no-index --find-links ./vendor/wheels --require-hashes --no-deps -r requirements.lock",
    }
    output = dest.parent / "BUNDLE_MANIFEST.json"
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    total = sum(item["size"] for item in wheels) / 1e6
    print(f"완료: wheel {len(wheels)}개 ({total:.0f} MB), 반입 목록 {output}")
    print("각 패키지의 라이선스는 반입 전에 회사 정책에 따라 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
