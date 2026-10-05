@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
REM =============================================================================
REM  setup.bat - Windows 용 설치 (인터넷 불필요)
REM
REM  이 폴더 안의 vendor\wheels (설치 패키지) 와 models (모델) 만으로 설치합니다.
REM  GitHub 릴리스의 설치 묶음(zip) 을 풀어 나온 patent_finder 폴더에서 실행하세요.
REM
REM  하는 일
REM    1) Python 3.11 ~ 3.13 (64비트) 찾기
REM    2) .venv 가상환경 만들기
REM    3) 설치 패키지 설치 (해시 확인)
REM    4) 점검
REM
REM  사용법        이 파일을 더블클릭  (다시 실행해도 됩니다)
REM  Python 지정   검은 창에서  set PM_PYTHON=py -3.12  입력 후  setup.bat
REM =============================================================================
set "CHECK=import sys,struct; raise SystemExit(0 if (3,11)<=sys.version_info[:2]<=(3,13) and struct.calcsize('P')==8 else 1)"

echo.
echo ============================================================
echo   특허 검토 후보 마킹 도구 - 설치 (인터넷 불필요)
echo ============================================================

echo.
echo [1/4] Python 확인
set "PY="
if not defined PM_PYTHON goto find_python
%PM_PYTHON% -c "%CHECK%" >nul 2>&1 && set "PY=%PM_PYTHON%"
if defined PY goto py_ok
echo    [실패] PM_PYTHON 으로 지정한 Python 을 쓸 수 없습니다: %PM_PYTHON%
echo           Python 3.11, 3.12, 3.13 (64비트) 중 하나여야 합니다.
pause
exit /b 1

:find_python
py -3.11 -c "%CHECK%" >nul 2>&1 && set "PY=py -3.11"
if not defined PY ( py -3.12 -c "%CHECK%" >nul 2>&1 && set "PY=py -3.12" )
if not defined PY ( py -3.13 -c "%CHECK%" >nul 2>&1 && set "PY=py -3.13" )
if not defined PY ( python -c "%CHECK%" >nul 2>&1 && set "PY=python" )
if defined PY goto py_ok
echo    [실패] Python 3.11, 3.12, 3.13 (64비트) 중 하나가 필요한데 찾지 못했습니다.
echo           이 PC 에 설치된 Python:
py -0p 2>nul
echo           위 목록이 비어 있거나 3.10 이하만 있으면 Python 을 먼저 설치해야 합니다.
pause
exit /b 1

:py_ok
%PY% "%~dp0tools\windows_setup.py"
if errorlevel 1 goto failed

echo.
echo ============================================================
echo   설치 완료!
echo     분석       mark.bat 더블클릭 - 파일 열기 창에서 PPTX, PDF 파일 고르기
echo                ^(파일이나 폴더를 mark.bat 아이콘 위에 끌어다 놓아도 됩니다^)
echo     검토 화면  run.bat 더블클릭
echo ============================================================
pause
exit /b 0

:failed
echo.
echo ============================================================
echo   설치가 끝나지 않았습니다. 위의 [실패] 안내를 확인하세요.
echo ============================================================
pause
exit /b 1
