"""파일 열기 창 (mark.bat을 더블클릭했을 때).

Python 표준 라이브러리의 tkinter로 운영체제의 '열기' 창을 띄운다. 브라우저를 거치지 않으므로
웹 업로드 차단과 무관하다. tkinter가 빠진 Python이면 PickerUnavailable을 낸다.
"""
from __future__ import annotations

import os
from pathlib import Path

TITLE = "분석할 파일 열기 (여러 개 선택 가능)"
FILE_TYPES = [
    ("보고자료 (PPTX, PDF, DOCX)", "*.pptx *.pdf *.docx"),
    ("PowerPoint", "*.pptx"),
    ("PDF", "*.pdf"),
    ("Word", "*.docx"),
    ("텍스트", "*.txt *.md"),
    ("모든 파일", "*.*"),
]
# 자동 점검용. 이 환경 변수가 있으면 창을 띄우지 않고 여기 적힌 경로를 고른 것으로 한다 ('|' 로 구분, 빈 값은 취소).
PRESET_ENV = "PM_PICK_PATHS"


class PickerUnavailable(RuntimeError):
    pass


def pick_files() -> list[str]:
    """열기 창에서 고른 경로 목록. 취소하면 빈 목록."""
    preset = os.environ.get(PRESET_ENV)
    if preset is not None:
        return [item for item in preset.split("|") if item]
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise PickerUnavailable("이 PC의 Python에는 파일 선택 창(tkinter)이 없습니다.") from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise PickerUnavailable(f"파일 선택 창을 열지 못했습니다: {exc}") from exc
    try:
        root.withdraw()  # 빈 기본 창은 숨기고 열기 창만 보인다
        root.attributes("-topmost", True)  # 다른 창 뒤로 숨지 않게 맨 앞으로
        root.update()
        # 시작 폴더를 정하지 않는다. Windows는 지난번에 연 폴더에서 다시 시작한다.
        picked = root.tk.splitlist(filedialog.askopenfilenames(parent=root, title=TITLE, filetypes=FILE_TYPES))
    finally:
        root.destroy()
    return [str(Path(item)) for item in picked if item]  # Windows의 tk는 C:/a/b처럼 슬래시로 준다
