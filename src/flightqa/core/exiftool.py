"""exiftool 바이너리 위치 탐색 및 단발 실행.

배치 추출(-stay_open)은 M1 의 ``core.metadata`` 가 pyexiftool 로 담당한다.
이 모듈은 (1) 번들본 우선, PATH 차선의 탐색 규칙(CLAUDE.md §9)과
(2) Windows 비-ASCII 경로를 안전하게 넘기는 단발 실행만 제공한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

EXE_NAME = "exiftool.exe" if sys.platform == "win32" else "exiftool"


class ExifToolNotFoundError(RuntimeError):
    """exiftool 바이너리를 찾지 못했을 때."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def bundled_candidates() -> list[Path]:
    """번들본 후보 경로. PyInstaller 로 얼린 경우 ``sys._MEIPASS`` 아래를 먼저 본다."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    roots.append(_repo_root())
    return [root / "tools" / "exiftool" / EXE_NAME for root in roots]


def find_exiftool(explicit: str | os.PathLike[str] | None = None) -> Path:
    """exiftool 실행 파일 경로를 반환한다.

    우선순위: 명시 경로 → 환경변수 ``FLIGHTQA_EXIFTOOL`` → 번들본 → PATH.
    """
    if explicit is not None:
        p = Path(explicit)
        if p.is_file():
            return p
        raise ExifToolNotFoundError(f"지정한 exiftool 경로가 없습니다: {p}")
    env = os.environ.get("FLIGHTQA_EXIFTOOL")
    if env and Path(env).is_file():
        return Path(env)
    for cand in bundled_candidates():
        if cand.is_file():
            return cand
    found = shutil.which("exiftool")
    if found:
        return Path(found)
    raise ExifToolNotFoundError(
        "exiftool 을 찾을 수 없습니다. scripts/fetch_exiftool.py 를 실행하거나 "
        "PATH 에 설치하거나 설정에서 경로를 지정하세요."
    )


def exiftool_version(exe: Path) -> str:
    out = subprocess.run([str(exe), "-ver"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def read_tags(
    files: list[Path],
    exe: Path | None = None,
    *,
    numeric: bool = False,
) -> list[dict[str, Any]]:
    """``exiftool -j -G1 -struct -a`` 로 파일들의 전체 태그를 읽는다 (단발 실행).

    파일 목록은 UTF-8 argfile(``-@``)과 ``-charset filename=utf8`` 로 넘긴다.
    Windows 에서 비-ASCII(한글) 경로를 argv 로 직접 넘기면 Perl 런타임이
    ANSI 코드페이지로 받아 파일을 열지 못한다 (docs/ASSUMPTIONS.md 참고).
    """
    if not files:
        return []
    exe = exe or find_exiftool()
    args = ["-charset", "filename=utf8", "-j", "-G1", "-struct", "-a"]
    if numeric:
        args.append("-n")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".args", delete=False) as fh:
        fh.write("\n".join([*args, *(str(f) for f in files)]) + "\n")
        argfile = Path(fh.name)
    try:
        proc = subprocess.run(
            [str(exe), "-@", str(argfile)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        argfile.unlink(missing_ok=True)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        # exiftool 은 일부 파일 실패 시 1 을 반환하되 JSON 은 정상 출력한다.
        raise RuntimeError(f"exiftool 실패 (rc={proc.returncode}): {proc.stderr.strip()}")
    data: list[dict[str, Any]] = json.loads(proc.stdout)
    return data
