"""Local CI: run formatter check, linter, type checker and tests in sequence.

Equivalent to ``make check``; provided as a script because ``make`` is not
available on a stock Windows machine.  Exit code is non-zero if any step fails.

    uv run python scripts/check.py            # all steps
    uv run python scripts/check.py lint test  # subset
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STEPS: dict[str, list[list[str]]] = {
    "format": [["uv", "run", "ruff", "format", "--check", "."]],
    "lint": [["uv", "run", "ruff", "check", "."]],
    "typecheck": [["uv", "run", "mypy"]],
    "test": [["uv", "run", "pytest"]],
}


def main(argv: list[str]) -> int:
    selected = argv or list(STEPS)
    unknown = [s for s in selected if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {', '.join(unknown)}; choose from {', '.join(STEPS)}")
        return 2
    results: list[tuple[str, bool, float]] = []
    for name in selected:
        t0 = time.perf_counter()
        ok = True
        for cmd in STEPS[name]:
            print(f"\n=== {name}: {' '.join(cmd)}")
            proc = subprocess.run(cmd, cwd=REPO, check=False)
            if proc.returncode != 0:
                ok = False
                break
        results.append((name, ok, time.perf_counter() - t0))
    print("\n=== summary")
    for name, ok, dt in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<10} {dt:6.1f}s")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
