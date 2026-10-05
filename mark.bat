@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
REM =============================================================================
REM  mark.bat - 끌어다 놓아 분석하기 (Windows)
REM    분석할 PPTX, PDF, DOCX 파일이나 폴더를 이 아이콘 위에 끌어다 놓으면 바로 분석합니다 (여러 개 가능).
REM    결과는 이 폴더의 outputs\mark-날짜-시각 안에 저장되고, 끝나면 그 폴더가 열립니다.
REM      report.html          표시된 구간 목록
REM      marked\              표시가 들어간 PPTX, PDF 사본
REM    원본 파일은 바뀌지 않습니다.
REM  (설치가 안 되어 있으면 먼저 setup.bat)
REM =============================================================================

REM 끌어다 놓은 파일 이름에 ^& 가 있고 공백이 없으면 (예: R^&D현황.pptx) Windows 가 이름을 ^& 앞에서 잘라 넘깁니다.
REM 잘리기 전의 명령줄 전체를 파이썬에 따로 알려 주어 원래 이름을 되살리게 합니다 (src\patent_marker\dropped.py).
setlocal EnableDelayedExpansion
set "PM_CMDLINE=!cmdcmdline!"
setlocal DisableDelayedExpansion

if "%~1"=="" (
  echo.
  echo   사용법: 분석할 PPTX, PDF 파일이나 폴더를 이 mark.bat 아이콘 위에 끌어다 놓으세요 ^(여러 개 가능^).
  echo   결과는 이 폴더의 outputs 안에 저장되고, 끝나면 그 폴더가 열립니다.
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행하세요.
  pause
  exit /b 1
)

set "PYTHONPATH=%~dp0src"
echo.
echo   특허 검토 후보 마킹 도구 - 분석
echo.
".venv\Scripts\python.exe" -m patent_marker.cli mark --open %*
set "RC=%ERRORLEVEL%"
echo.
pause
REM 이름이 ^& 에서 잘렸던 경우, 잘린 뒷부분을 Windows 가 명령으로 실행하려 들지 않게 여기서 창을 닫습니다.
setlocal EnableDelayedExpansion
if not "!PM_CMDLINE:&=!"=="!PM_CMDLINE!" exit !RC!
exit /b !RC!
