#!/usr/bin/env bash
# FlightQA 실행 (macOS/Linux, Python 환경용). exe/.app 배포본 안에서는 같은 폴더의 FlightQA 를 실행.
set -e
cd "$(dirname "$0")"
if [ -x "./FlightQA" ]; then exec ./FlightQA "$@"; fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv 가 없습니다. https://docs.astral.sh/uv/ 참고 (pip install uv)"; exit 1
fi
uv sync --all-groups
exec uv run flightqa-gui "$@"
