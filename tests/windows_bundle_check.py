#!/usr/bin/env python3
"""설치 묶음의 batch 파일을 Windows에서 끝까지 돌려 보는 점검 (pytest 시험이 아니다).

GitHub의 Windows 러너에서 묶음을 푼 폴더 안에서 실행한다(.github/workflows/windows.yml).

    python tests\\windows_bundle_check.py --expect 3.12
    python tests\\windows_bundle_check.py --expect 3.12 --moved     (폴더를 다른 곳으로 옮긴 뒤)

하는 일
    1) setup.bat      더블클릭했을 때처럼 실행. 다시 실행해도 되는지도 본다
    2) mark.bat       더블클릭하면 파일 열기 창이 뜨는지, 고른 파일을 분석하는지.
                      탐색기가 파일을 끌어다 놓을 때 만드는 명령줄 그대로도 본다 (공백·한글·괄호·&가 든 이름, 폴더)
    3) run.bat        검토 화면을 띄워 접속되는지

표준 라이브러리만 쓴다. batch 파일의 pause는 입력을 닫아 두면 바로 지나간다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "synthetic"
RESULTS: list[tuple[str, bool, str]] = []


def note(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  ({detail})" if detail else ""), flush=True)
    return bool(ok)


def run(command: list[str] | str, env: dict[str, str], timeout: int, label: str) -> tuple[int, str]:
    print(f"\n----- {label} -----", flush=True)
    if isinstance(command, str):
        print(command, flush=True)
    try:
        process = subprocess.run(command, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        code, raw = process.returncode, process.stdout
    except subprocess.TimeoutExpired as expired:
        code, raw = -1, (expired.stdout or b"") + b"\n[timeout]"
    text = raw.decode("utf-8", errors="replace")
    print(text, flush=True)
    return code, text


def drop(batch: str, paths: list[Path]) -> str:
    """탐색기가 파일을 batch 파일 위에 끌어다 놓을 때 만드는 명령줄. 공백이 든 경로만 따옴표로 감싼다."""
    arguments = " ".join(f'"{path}"' if " " in str(path) else str(path) for path in paths)
    return f'cmd /c ""{ROOT / batch}" {arguments}"'


def venv_version(python: Path) -> str:
    result = subprocess.run([str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                            capture_output=True, text=True, timeout=60, check=False)
    return result.stdout.strip()


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def close_window(title: str, timeout: float) -> bool:
    """제목이 title인 창이 뜰 때까지 기다렸다가 닫는다(사용자가 창의 X를 누른 것과 같다). 창을 찾았으면 True."""
    import ctypes

    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        handle = user32.FindWindowW(None, title)
        if handle:
            time.sleep(1.5)  # 창이 다 그려질 시간을 준다
            user32.PostMessageW(handle, 0x0010, 0, 0)  # WM_CLOSE
            return True
        time.sleep(0.5)
    return False


def stop(process: subprocess.Popen) -> None:
    """run.bat이 띄운 파이썬까지 함께 끝낸다."""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--expect", required=True, help="가상환경이 쓰게 될 Python 버전 (예: 3.12)")
    parser.add_argument("--moved", action="store_true", help="설치한 폴더를 옮긴 뒤의 점검")
    args = parser.parse_args()
    if os.name != "nt":
        print("이 점검은 Windows에서만 돈다.")
        return 2

    env = dict(os.environ, PM_NO_OPEN="1")
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    print(f"폴더: {ROOT}\nPM_PYTHON: {env.get('PM_PYTHON') or '(지정 안 함: setup.bat이 찾는다)'}", flush=True)
    note("묶음 구성: batch 파일, 설치 패키지, 모델이 있음",
         all((ROOT / name).exists() for name in ("setup.bat", "mark.bat", "run.bat", "vendor/wheels",
                                                  "models/multilingual-e5-small/model.onnx", "requirements.lock")))

    # ---- 1. setup.bat ---------------------------------------------------------
    if args.moved:
        note("옮긴 뒤: 가상환경이 그대로 있음", python.is_file())
    else:
        note("처음 상태: 가상환경이 아직 없음", not python.exists())
        code, out = run(["cmd", "/c", "setup.bat"], env, 1800, "setup.bat")
        note("setup.bat: 끝까지 실행 (종료 코드 0)", code == 0, f"종료 코드 {code}")
        steps = [step for step in ("[1/4]", "[2/4]", "[3/4]", "[4/4]") if step in out]
        note("setup.bat: 네 단계와 '설치 완료!'가 한글로 찍힘", len(steps) == 4 and "설치 완료!" in out and "가상환경" in out,
             " ".join(steps))
        note("setup.bat: 점검에 실패 항목이 없음", "실패 0개" in out and "[FAIL]" not in out)
    note(f"가상환경이 Python {args.expect}로 만들어짐", python.is_file() and venv_version(python) == args.expect,
         venv_version(python) if python.is_file() else "가상환경 없음")
    code, out = run(["cmd", "/c", "setup.bat"], env, 1800, "setup.bat (다시 실행)")
    note("setup.bat 다시 실행: 가상환경을 그대로 쓰고 끝까지 감", code == 0 and "이미 있음" in out and "설치 완료!" in out,
         f"종료 코드 {code}")

    # ---- 2. mark.bat (더블클릭 → 파일 열기 창) -----------------------------------
    sys.path.insert(0, str(ROOT / "src"))
    from patent_marker.pickdialog import PRESET_ENV, TITLE

    result = subprocess.run([str(python), "-c", "import tkinter; r = tkinter.Tk(); r.withdraw(); r.destroy(); print('ok')"],
                            capture_output=True, text=True, timeout=120, check=False)
    note("가상환경의 Python에 파일 선택 창 부품(tkinter)이 있음", result.returncode == 0 and "ok" in result.stdout,
         (result.stderr or "").strip().splitlines()[-1] if result.returncode else "")

    print("\n----- mark.bat (더블클릭: 열기 창이 뜨면 닫는다) -----", flush=True)
    log_path = Path(tempfile.gettempdir()) / "pf-mark-pick.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(["cmd", "/c", "mark.bat"], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        appeared = close_window(TITLE, 90)
        try:
            code = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            code = -1
            stop(process)
    out = log_path.read_bytes().decode("utf-8", errors="replace")
    print(out, flush=True)
    note("mark.bat 더블클릭: 파일 열기 창이 뜸", appeared)
    note("mark.bat 더블클릭: 창을 닫으면 분석하지 않고 끝남", code == 0 and "파일을 고르지 않았습니다" in out, f"종료 코드 {code}")

    work = Path(tempfile.mkdtemp(prefix="pf-drop-"))
    ampersand = work / "R&D현황.pptx"
    spaced = work / "최종 보고서 (2차).pdf"
    folder = work / "자료모음"
    folder.mkdir()
    shutil.copyfile(FIXTURES / "sample_report.pptx", ampersand)
    shutil.copyfile(FIXTURES / "sample_report.pdf", spaced)
    shutil.copyfile(FIXTURES / "sample_report.docx", folder / "검토 메모.docx")
    shutil.copyfile(FIXTURES / "sample_notes.txt", folder / "노트.txt")
    before = {path.name for path in (ROOT / "outputs").glob("mark-*")} if (ROOT / "outputs").is_dir() else set()
    # 열기 창에서 파일 둘을 고른 것으로 하고(창은 띄우지 않는다) 끝까지 분석되는지
    code, out = run(["cmd", "/c", "mark.bat"], dict(env, **{PRESET_ENV: f"{ampersand}|{spaced}"}), 1800,
                    "mark.bat (더블클릭: 열기 창에서 파일 둘을 고름)")
    picked = sorted(path for path in (ROOT / "outputs").glob("mark-*") if path.name not in before)
    marked = sorted(path.name for path in (picked[0] / "marked").iterdir()) if picked and (picked[0] / "marked").is_dir() else []
    note("mark.bat 더블클릭: 고른 파일을 분석해 마킹 사본을 만듦",
         code == 0 and marked == ["R&D현황.marked.pptx", "최종 보고서 (2차).marked.pdf"], f"종료 코드 {code} · {', '.join(marked)}")
    before = {path.name for path in (ROOT / "outputs").glob("mark-*")}

    # ---- 2-2. mark.bat (끌어다 놓기) --------------------------------------------
    code, out = run(drop("mark.bat", [ampersand, spaced, folder]), env, 1800,
                    "mark.bat (파일 둘과 폴더 하나를 끌어다 놓음: &, 공백, 괄호, 한글)")
    note("mark.bat 끌어다 놓기: 끝까지 실행 (종료 코드 0)", code == 0, f"종료 코드 {code}")
    note("mark.bat: 검은 창에 진행 상황과 결과 폴더가 한글로 찍힘", "결과 폴더:" in out and "R&D현황.pptx" in out)
    created = sorted(path for path in (ROOT / "outputs").glob("mark-*") if path.name not in before)
    note("mark.bat: 결과 폴더가 하나 생김", len(created) == 1, ", ".join(path.name for path in created))
    if created:
        output = created[0]
        marked = sorted(path.name for path in (output / "marked").iterdir()) if (output / "marked").is_dir() else []
        note("mark.bat 결과: 이름이 &에서 잘리지 않고 PPTX·PDF 마킹 사본이 생김",
             marked == ["R&D현황.marked.pptx", "최종 보고서 (2차).marked.pdf"], ", ".join(marked))
        note("mark.bat 결과: report.html과 results.jsonl", (output / "report.html").is_file()
             and (output / "results.jsonl").is_file())
        if (output / "results.jsonl").is_file():
            rows = [json.loads(line) for line in (output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
            names = sorted({row["file_name"] for row in rows})
            note("mark.bat 결과: 폴더 안의 DOCX·TXT까지 네 문서가 모두 분석됨",
                 names == ["R&D현황.pptx", "검토 메모.docx", "노트.txt", "최종 보고서 (2차).pdf"], ", ".join(names))
            note("mark.bat 결과: 후보로 표시된 구간이 있음",
                 any(row["final_decision"] == "CANDIDATE" for row in rows), f"구간 {len(rows)}개")
    note("mark.bat: 원본 파일은 그대로", ampersand.read_bytes() == (FIXTURES / "sample_report.pptx").read_bytes()
         and spaced.read_bytes() == (FIXTURES / "sample_report.pdf").read_bytes())
    shutil.rmtree(work, ignore_errors=True)

    # ---- 3. run.bat (검토 화면) -------------------------------------------------
    print("\n----- run.bat -----", flush=True)
    log_path = Path(tempfile.gettempdir()) / "pf-run-bat.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(["cmd", "/c", "run.bat"], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        status, body, error = 0, "", ""
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                status, body = http_get("http://127.0.0.1:8765/")
                break
            except (urllib.error.URLError, OSError) as exc:
                error = str(exc)
                if process.poll() is not None:
                    break
                time.sleep(1)
        stop(process)
    print(log_path.read_bytes().decode("utf-8", errors="replace"), flush=True)
    note("run.bat: 검토 화면이 127.0.0.1:8765에서 열림", status == 200 and "<html" in body.lower(),
         f"HTTP {status}" if status else error)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n점검 {len(RESULTS)}개 · 통과 {len(RESULTS) - len(failed)}개 · 실패 {len(failed)}개", flush=True)
    for name in failed:
        print("  실패:", name, flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
