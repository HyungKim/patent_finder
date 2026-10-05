#!/usr/bin/env python3
"""설치 본체. setup.bat이 찾은 Python으로 실행한다 (인터넷 불필요).

이 폴더 안의 vendor/wheels(설치 패키지)와 models(모델)만 쓴다.

    1) 이 Python 버전용 설치 패키지가 묶음에 있는지 확인
    2) .venv 가상환경 만들기 (이미 있고 쓸 수 있으면 그대로 둔다)
    3) 설치 패키지 설치 (해시 확인, 인터넷 사용 안 함)
    4) 점검 (doctor --offline)

- 표준 라이브러리만 쓴다. 운영 코드(src/patent_marker)는 이 파일을 import하지 않는다.
- 다시 실행해도 된다. 이미 된 단계는 빠르게 지나간다.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUPPORTED = ((3, 11), (3, 12), (3, 13))
# 버전마다 파일이 따로 있는 패키지. 이 파일이 있으면 그 버전용 설치 패키지가 묶음에 들어 있는 것으로 본다.
PROBE_PACKAGE = "numpy"


def python_tag(version: tuple[int, int] | None = None) -> str:
    major, minor = version or sys.version_info[:2]
    return f"cp{major}{minor}"


def is_supported(version: tuple[int, int] | None = None, pointer_bytes: int | None = None) -> bool:
    version = version or sys.version_info[:2]
    pointer_bytes = pointer_bytes or struct.calcsize("P")
    return tuple(version) in SUPPORTED and pointer_bytes == 8


def has_wheels_for(tag: str, wheel_dir: Path) -> bool:
    return any(wheel_dir.glob(f"{PROBE_PACKAGE}-*-{tag}-{tag}-*.whl"))


def bundled_tags(wheel_dir: Path) -> list[str]:
    tags = {path.name.split("-")[2] for path in wheel_dir.glob(f"{PROBE_PACKAGE}-*.whl")}
    return sorted(tags)


def venv_python(root: Path = ROOT) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def venv_usable(python: Path, tag: str) -> bool:
    """가상환경이 실행되고 같은 Python 버전으로 만들어졌는지."""
    if not python.is_file():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", "import sys; print('cp%d%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == tag


def install_command(python: Path, root: Path = ROOT) -> list[str]:
    return [
        str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
        "--find-links", str(root / "vendor" / "wheels"), "--require-hashes", "--no-deps", "--no-cache-dir",
        "-r", str(root / "requirements.lock"),
    ]


def say(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str, *hints: str) -> int:
    say(f"   [실패] {message}")
    for hint in hints:
        say(f"          {hint}")
    return 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    tag = python_tag()
    wheel_dir = ROOT / "vendor" / "wheels"
    version = ".".join(map(str, sys.version_info[:3]))
    say(f"   Python {version} ({struct.calcsize('P') * 8}비트) · {sys.executable}")
    if not is_supported():
        return fail("이 Python은 쓸 수 없습니다. Python 3.11, 3.12, 3.13 (64비트) 중 하나가 필요합니다.")
    if not (ROOT / "requirements.lock").is_file() or not wheel_dir.is_dir():
        return fail("이 폴더에 설치 패키지(vendor\\wheels)가 없습니다.",
                    "GitHub 릴리스의 설치 묶음(zip)을 받아 그 안의 patent_finder 폴더에서 실행하세요.",
                    "저장소 화면의 Download ZIP에는 설치 패키지와 모델이 들어 있지 않습니다.")
    if not has_wheels_for(tag, wheel_dir):
        available = ", ".join(bundled_tags(wheel_dir)) or "없음"
        return fail(f"이 묶음에는 Python {sys.version_info[0]}.{sys.version_info[1]}용 설치 패키지가 없습니다. (들어 있는 것: {available})",
                    "들어 있는 버전의 Python으로 다시 실행하세요. 예: set PM_PYTHON=py -3.12 입력 후 setup.bat")

    say()
    say("[2/4] 가상환경(.venv) 만들기")
    python = venv_python()
    if venv_usable(python, tag):
        say("   이미 있음")
    else:
        result = subprocess.run([sys.executable, "-m", "venv", "--clear", str(ROOT / ".venv")], check=False)
        if result.returncode != 0 or not venv_usable(python, tag):
            return fail("가상환경을 만들지 못했습니다.", "이 폴더에 쓰기 권한이 있는지, 백신이 막지 않는지 확인하세요.")
        say("   완료")

    say()
    say("[3/4] 설치 패키지 설치 (인터넷 사용 안 함)")
    result = subprocess.run(install_command(python), cwd=str(ROOT), check=False)
    if result.returncode != 0:
        return fail("설치 패키지를 설치하지 못했습니다. 위 오류의 마지막 몇 줄을 적어 두세요.",
                    "'HASH' 나 'hash' 가 보이면 받은 파일이 손상된 것입니다. 묶음을 다시 받으세요.")
    say("   완료")

    say()
    say("[4/4] 점검")
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    result = subprocess.run([str(python), "-m", "patent_marker.cli", "doctor", "--offline"], cwd=str(ROOT),
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    report = result.stdout.decode("utf-8", errors="replace")
    say(report.rstrip())
    if result.returncode != 0:
        hints = ["위에서 [FAIL] 로 표시된 줄을 확인하세요."]
        if "DLL" in report:
            hints.append("DLL 오류가 보이면 'Microsoft Visual C++ 재배포 가능 패키지 (x64)' 가 필요합니다.")
        return fail("점검을 통과하지 못했습니다.", *hints)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
