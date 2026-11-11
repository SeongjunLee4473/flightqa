#!/usr/bin/env bash
# FlightQA macOS 빌드 (CLAUDE.md §9): PyInstaller onedir .app. 코드서명 없음 → README 의 실행 안내(우클릭→열기).
# 요구: uv, brew install exiftool (번들 대신 시스템 exiftool 사용 — PATH 탐색)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1. 의존성"; uv sync --all-groups
echo "== 2. 빌드 정보·아이콘"; uv run python packaging/write_build_info.py; uv run python packaging/make_icon.py || true
echo "== 3. PyInstaller"
rm -rf dist/FlightQA dist/FlightQA.app
uv run pyinstaller packaging/flightqa.spec --noconfirm --clean --distpath dist --workpath build/pyinstaller
echo "== 4. 동봉"
cp README.md packaging/run.sh dist/FlightQA/
mkdir -p dist/FlightQA/docs && cp docs/QA_RULES.md docs/ASSUMPTIONS.md dist/FlightQA/docs/
echo "== 5. 스모크"
dist/FlightQA/flightqa-cli version
ver=$(uv run python -c "import flightqa; print(flightqa.__version__)")
echo "== 6. zip"
(cd dist && zip -qr "FlightQA-${ver}-macos.zip" FlightQA)
ls -la "dist/FlightQA-${ver}-macos.zip"
echo "완료. 실행: dist/FlightQA/FlightQA (처음 실행은 우클릭→열기, Gatekeeper)"
