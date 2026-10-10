"""Windows 설치·실행 batch 파일과 그 뒤에서 도는 도구 시험.

batch 파일을 실제로 실행하는 확인은 GitHub의 Windows 러너에서 한다(.github/workflows/windows.yml).
여기서는 어느 OS에서나 볼 수 있는 것만 본다: 파일 형식, 끌어다 놓은 이름 되살리기, 설치 본체의 판단.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from helpers import FIXTURES, REPO_ROOT
from patent_marker import pickdialog
from patent_marker.analysis import run_analysis
from patent_marker.cli import main
from patent_marker.dropped import recover_dropped

BATCH_FILES = ("setup.bat", "mark.bat", "run.bat", "train.bat")


def _load_setup_tool():
    spec = importlib.util.spec_from_file_location("windows_setup", REPO_ROOT / "tools" / "windows_setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- batch 파일 형식
@pytest.mark.parametrize("name", BATCH_FILES)
def test_batch_files_are_crlf_utf8_and_switch_the_console_to_utf8(name):
    raw = (REPO_ROOT / name).read_bytes()
    text = raw.decode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")  # BOM이 있으면 첫 줄 @echo off가 깨진다
    assert raw.count(b"\n") == raw.count(b"\r\n") > 10  # 모든 줄이 CRLF여야 cmd가 label을 제대로 찾는다
    lines = text.split("\r\n")
    assert lines[0] == "@echo off" and lines[1] == "chcp 65001 >nul"
    assert 'cd /d "%~dp0"' in lines[:5]  # 어느 폴더에서 실행해도 자기 폴더 기준으로 돈다


def test_windows_readme_is_crlf_and_names_the_batch_files():
    path = next(REPO_ROOT.glob("*설치순서.txt"))
    raw = path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 10
    text = raw.decode("utf-8")
    assert all(name in text for name in BATCH_FILES)


def test_batch_files_point_at_things_that_exist():
    setup = (REPO_ROOT / "setup.bat").read_bytes().decode("utf-8")
    assert r"tools\windows_setup.py" in setup and (REPO_ROOT / "tools" / "windows_setup.py").is_file()
    for version in ("3.11", "3.12", "3.13"):
        assert f"py -{version}" in setup
    mark = (REPO_ROOT / "mark.bat").read_bytes().decode("utf-8")
    assert "-m patent_marker.cli mark --open --pick %*" in mark and "PM_CMDLINE" in mark
    run = (REPO_ROOT / "run.bat").read_bytes().decode("utf-8")
    assert "-m patent_marker.cli review --port %PORT%" in run
    train = (REPO_ROOT / "train.bat").read_bytes().decode("utf-8")
    assert "-m patent_marker.cli retrain %*" in train and "pause" in train
    for text in (mark, run, train):
        assert r'set "PYTHONPATH=%~dp0src"' in text and r".venv\Scripts\python.exe" in text


# ---------------------------------------------------------------- 끌어다 놓은 이름
def test_dropped_names_are_recovered_from_the_raw_command_line(tmp_path):
    amp = tmp_path / "R&D현황.pptx"
    spaced = tmp_path / "최종 보고서 (2차).pdf"
    folder = tmp_path / "자료모음"
    for path in (amp, spaced):
        path.write_bytes(b"x")
    folder.mkdir()
    bat = r"D:\patent_finder\mark.bat"
    # 탐색기는 공백이 든 경로만 따옴표로 감싼다. cmd는 따옴표 밖의 & 앞에서 잘라 batch에 넘긴다.
    cmdline = f'C:\\WINDOWS\\system32\\cmd.exe /c ""{bat}" {amp} "{spaced}" {folder}"'
    truncated = [str(tmp_path / "R")]
    assert recover_dropped(truncated, cmdline) == [str(amp), str(spaced), str(folder)]
    assert recover_dropped([str(spaced)], cmdline) == [str(spaced)]  # 넘어온 경로가 모두 있으면 손대지 않는다
    assert recover_dropped(truncated, None) == truncated
    assert recover_dropped(truncated, "python -m something") == truncated  # batch 이름이 없는 명령줄
    assert recover_dropped([], f'cmd /c ""{bat}" {amp}"') == [str(amp)]
    assert recover_dropped([], None) == []


def test_mark_command_reports_missing_paths_without_loading_the_model(tmp_path, capsys):
    config = tmp_path / "config.yaml"
    config.write_text("paths:\n  base_dir: .\n  e5: models/not-there\nruntime:\n  offline: false\n", encoding="utf-8")
    assert main(["mark", "--config", str(config), str(tmp_path / "없는파일.pptx")]) == 1
    assert "분석할 파일을 찾지 못했습니다" in capsys.readouterr().out
    assert main(["mark", "--config", str(config)]) == 1


# ---------------------------------------------------------------- 파일 열기 창
def _fake_tkinter(monkeypatch, selection):
    """tkinter를 흉내 낸다. 열기 창에 넘어간 인자와 정리 여부를 calls에 남긴다."""
    calls: dict = {}

    class FakeRoot:
        def __init__(self):
            self.tk = self

        def withdraw(self):
            calls["withdrawn"] = True

        def attributes(self, *args):
            calls["attributes"] = args

        def update(self):
            pass

        def destroy(self):
            calls["destroyed"] = True

        def splitlist(self, value):
            return value

    def askopenfilenames(**kwargs):
        calls["dialog"] = kwargs
        return selection

    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tk = FakeRoot
    fake_tk.TclError = RuntimeError
    fake_dialog = types.ModuleType("tkinter.filedialog")
    fake_dialog.askopenfilenames = askopenfilenames
    fake_tk.filedialog = fake_dialog
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", fake_dialog)
    monkeypatch.delenv(pickdialog.PRESET_ENV, raising=False)
    return calls


def test_picker_opens_a_multi_select_dialog_and_returns_native_paths(monkeypatch):
    calls = _fake_tkinter(monkeypatch, ("C:/자료/R&D 현황.pptx", "C:/자료/b.pdf"))
    assert pickdialog.pick_files() == [str(Path("C:/자료/R&D 현황.pptx")), str(Path("C:/자료/b.pdf"))]
    assert calls["dialog"]["title"] == pickdialog.TITLE and calls["withdrawn"] and calls["destroyed"]
    assert calls["attributes"] == ("-topmost", True)  # 다른 창 뒤에 숨지 않게
    assert "initialdir" not in calls["dialog"]  # 시작 폴더를 정하지 않아 Windows가 지난번 폴더에서 연다
    patterns = " ".join(pattern for _, pattern in calls["dialog"]["filetypes"])
    assert all(extension in patterns for extension in ("*.pptx", "*.pdf", "*.docx"))


def test_picker_cancel_preset_and_missing_tkinter(monkeypatch):
    _fake_tkinter(monkeypatch, "")  # 창을 그냥 닫으면 빈 문자열이 온다
    assert pickdialog.pick_files() == []
    monkeypatch.setenv(pickdialog.PRESET_ENV, "a.pptx|b.pdf")  # 자동 점검용: 창을 띄우지 않는다
    assert pickdialog.pick_files() == ["a.pptx", "b.pdf"]
    monkeypatch.delenv(pickdialog.PRESET_ENV)
    monkeypatch.setitem(sys.modules, "tkinter", None)  # tkinter가 빠진 Python
    with pytest.raises(pickdialog.PickerUnavailable, match="tkinter"):
        pickdialog.pick_files()


def test_mark_pick_handles_cancel_and_missing_tkinter_without_loading_the_model(tmp_path, capsys, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("paths:\n  base_dir: .\n  e5: models/not-there\nruntime:\n  offline: false\n", encoding="utf-8")
    monkeypatch.setenv(pickdialog.PRESET_ENV, "")  # 열기 창에서 취소
    assert main(["mark", "--config", str(config), "--pick"]) == 0
    assert "파일을 고르지 않았습니다" in capsys.readouterr().out
    monkeypatch.delenv(pickdialog.PRESET_ENV)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    assert main(["mark", "--config", str(config), "--pick"]) == 1
    out = capsys.readouterr().out
    assert "tkinter" in out and "끌어다 놓는 방법" in out


def test_analysis_accepts_several_files_and_folders(services, tmp_path):
    folder = tmp_path / "자료"
    folder.mkdir()
    (folder / "a.pptx").write_bytes((FIXTURES / "sample_report.pptx").read_bytes())
    single = tmp_path / "노트.txt"
    single.write_bytes((FIXTURES / "sample_notes.txt").read_bytes())
    summary = run_analysis(services, [single, folder, single], "run-multi")  # 같은 경로를 두 번 줘도 한 번만 처리
    assert [Path(item["path"]).name for item in summary["files"]] == ["노트.txt", "a.pptx"]
    assert summary["status"] == "COMPLETED" and summary["predicted"] == summary["segments"] > 0
    details = json.loads(services.database.query_one("SELECT details_json FROM runs WHERE run_id = 'run-multi'")[0])
    assert str(single.resolve()) in details["input"] and str(folder.resolve()) in details["input"]


# ---------------------------------------------------------------- 설치 본체 (tools/windows_setup.py)
def test_setup_tool_decides_python_and_wheels(tmp_path):
    tool = _load_setup_tool()
    assert tool.python_tag((3, 12)) == "cp312"
    assert tool.is_supported((3, 11), 8) and tool.is_supported((3, 13), 8)
    assert not tool.is_supported((3, 10), 8) and not tool.is_supported((3, 14), 8)
    assert not tool.is_supported((3, 12), 4)  # 32비트 Python
    wheels = tmp_path / "vendor" / "wheels"
    wheels.mkdir(parents=True)
    for name in ("numpy-2.4.6-cp311-cp311-win_amd64.whl", "numpy-2.4.6-cp313-cp313-win_amd64.whl",
                 "tokenizers-0.23.2-cp310-abi3-win_amd64.whl"):
        (wheels / name).write_bytes(b"")
    assert tool.has_wheels_for("cp311", wheels) and not tool.has_wheels_for("cp312", wheels)
    assert tool.bundled_tags(wheels) == ["cp311", "cp313"]


def test_setup_tool_installs_offline_with_hashes_and_no_cache(tmp_path):
    tool = _load_setup_tool()
    command = tool.install_command(Path("py.exe"), tmp_path)
    assert command[:4] == ["py.exe", "-m", "pip", "install"]
    for flag in ("--no-index", "--require-hashes", "--no-deps", "--no-cache-dir"):
        assert flag in command  # 인터넷을 쓰지 않고, 해시를 확인하고, 사용자 폴더에 캐시를 남기지 않는다
    assert command[command.index("--find-links") + 1] == str(tmp_path / "vendor" / "wheels")
    assert command[command.index("-r") + 1] == str(tmp_path / "requirements.lock")


def test_setup_tool_recognizes_a_usable_environment(tmp_path):
    tool = _load_setup_tool()
    assert tool.venv_usable(Path(sys.executable), tool.python_tag())
    assert not tool.venv_usable(Path(sys.executable), "cp399")  # 다른 버전으로 만든 가상환경은 다시 만든다
    assert not tool.venv_usable(tmp_path / "nope" / "python.exe", tool.python_tag())
