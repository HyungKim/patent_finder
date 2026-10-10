@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
REM =============================================================================
REM  train.bat - 판정으로 다시 학습 (Windows)
REM    run.bat 의 검토 화면에서 남긴 YES / NO 판정으로 분류기를 다시 학습하고, 지금 모델과 비교해 보여 줍니다.
REM    새 모델을 쓸지는 마지막에 묻습니다. y 를 누르고 Enter 를 쳐야 다음 분석(mark.bat)부터 새 모델을 씁니다.
REM    이미 분석한 결과는 바뀌지 않습니다. 되돌리는 방법은 docs\OFFLINE_INSTALL.md 에 있습니다.
REM  (설치가 안 되어 있으면 먼저 setup.bat)
REM =============================================================================

if not exist ".venv\Scripts\python.exe" (
  echo 먼저 setup.bat 을 실행하세요.
  pause
  exit /b 1
)

set "PYTHONPATH=%~dp0src"
echo.
echo   특허 검토 후보 마킹 도구 - 판정으로 다시 학습
echo   (검토 화면 run.bat 은 닫아 두세요)
echo.
".venv\Scripts\python.exe" -m patent_marker.cli retrain %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
