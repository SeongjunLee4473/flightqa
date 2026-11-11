# 로컬 CI. Windows 에서 make 가 없으면:  uv run python scripts/check.py
.PHONY: check lint typecheck test format sync tags exiftool

UV ?= uv

sync:
	$(UV) sync --all-groups

check:
	$(UV) run python scripts/check.py

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

exiftool:
	$(UV) run python scripts/fetch_exiftool.py
