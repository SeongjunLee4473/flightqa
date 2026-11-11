"""빌드 정보(git 해시·빌드일)를 src/flightqa/_build.py 로 쓴다.

앱 정보 창과 보고서 부록에 표시된다 (CLAUDE.md §9).
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "flightqa" / "_build.py"


def main() -> int:
    try:
        git = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git = ""
    TARGET.write_text(
        '"""빌드 시 자동 생성 (packaging/write_build_info.py). 커밋하지 않는다."""\n\n'
        f'GIT_HASH = "{git or "unknown"}"\n'
        f'BUILD_DATE = "{datetime.now(UTC):%Y-%m-%d %H:%M}Z"\n',
        encoding="utf-8",
    )
    print("build info:", git or "unknown", "->", TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(main())
