@echo off
rem FlightQA 실행 (Python 환경용). exe 가 기관 PC 에서 차단될 때 대안 (CLAUDE.md §9).
rem 1) exe 배포본 안에서: 같은 폴더의 FlightQA.exe 를 실행
rem 2) 소스 트리에서: uv 로 가상환경을 만들고 GUI 실행
setlocal
cd /d "%~dp0"
if exist "FlightQA.exe" (
  start "" "FlightQA.exe" %*
  goto :eof
)
where uv >nul 2>nul
if errorlevel 1 (
  echo uv 가 없습니다. https://docs.astral.sh/uv/ 에서 설치하거나 pip install uv 를 실행하세요.
  pause
  goto :eof
)
uv sync --all-groups
uv run flightqa-gui %*
