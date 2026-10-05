"""batch 파일 위에 끌어다 놓은 파일 이름 되살리기 (mark.bat).

Windows 탐색기는 공백이 든 경로만 따옴표로 감싸 넘긴다. 그래서 `C:\\문서\\R&D현황.pptx` 처럼 `&` 가 있고
공백이 없는 이름은 cmd가 `&` 앞(`C:\\문서\\R`)까지만 batch에 넘기고 뒷부분은 다른 명령으로 실행하려 든다.
mark.bat은 잘리기 전의 명령줄 전체(`%cmdcmdline%`)를 환경 변수 PM_CMDLINE으로 넘겨 주고, 여기서 경로를 다시 읽는다.
"""
from __future__ import annotations

import re
from pathlib import Path


def recover_dropped(paths: list[str], cmdline: str | None, script_name: str = "mark.bat") -> list[str]:
    """넘어온 경로 중 없는 것이 있으면 원래 명령줄에서 실제로 있는 경로를 다시 찾는다.

    넘어온 경로가 모두 있으면 그대로 돌려준다. 되살린 쪽이 더 많이 맞을 때만 바꾼다.
    """
    existing = sum(Path(item).exists() for item in paths)
    if not cmdline or (paths and existing == len(paths)):
        return paths
    match = re.search(re.escape(script_name) + r'"?\s+(.*)$', cmdline, re.IGNORECASE | re.DOTALL)
    if not match:
        return paths
    rest = match.group(1).strip()
    if rest.endswith('"') and rest.count('"') % 2 == 1:  # cmd /c ""...mark.bat" 파일들" 의 바깥 따옴표
        rest = rest[:-1]
    found = [quoted or bare for quoted, bare in re.findall(r'"([^"]*)"|(\S+)', rest)]
    found = [item for item in found if Path(item).exists()]
    return found if len(found) > existing else paths
