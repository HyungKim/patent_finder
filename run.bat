@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
REM =============================================================================
REM  run.bat - 검토 화면 실행 (Windows)
REM    분석한 결과를 브라우저에서 보고 YES / NO 판정을 남기는 화면입니다.
REM    웹 서버를 이 PC 안에서만 띄우고 브라우저를 엽니다  ->  http://127.0.0.1:8765
REM  종료: 이 창을 닫거나 Ctrl + C
REM  (설치가 안 되어 있으면 먼저 setup.bat)
REM =============================================================================
if "%PM_PORT%"=="" (set "PORT=8765") else (set "PORT=%PM_PORT%")

if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행하세요.
  pause
  exit /b 1
)

set "PYTHONPATH=%~dp0src"
echo.
echo   특허 검토 후보 마킹 도구 - 검토 화면  http://127.0.0.1:%PORT%
echo   (이 창을 닫으면 검토 화면이 종료됩니다)
echo.
REM 3초 뒤 브라우저를 연다 (서버가 뜰 시간을 줌). /D: 브라우저를 이 폴더가 아니라 홈 폴더에서 띄운다 (폴더를 옮길 때 잠기지 않게)
if not defined PM_NO_OPEN start "" /D "%USERPROFILE%" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"
".venv\Scripts\python.exe" -m patent_marker.cli review --port %PORT%
pause
