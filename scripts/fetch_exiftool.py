"""Download the pinned ExifTool Windows build into tools/exiftool/.

The binary is not committed (35 MB).  It is bundled into the frozen app by
packaging/flightqa.spec and used in development via flightqa.core.exiftool.
On macOS/Linux install exiftool with the system package manager instead.

    uv run python scripts/fetch_exiftool.py
"""

from __future__ import annotations

import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

EXIFTOOL_VERSION = "13.59"
URL = f"https://sourceforge.net/projects/exiftool/files/exiftool-{EXIFTOOL_VERSION}_64.zip/download"
# sha256 of exiftool-13.59_64.zip, measured 2026-09-10.
SHA256 = "44b512b25af500724ba579d0a53c8fc5851628b692dd5e5d94ae4a15c2cba9ec"

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "tools" / "exiftool"


def main() -> int:
    if sys.platform != "win32":
        print("Windows-only: on macOS/Linux install exiftool via brew/apt.")
        return 0
    if (TARGET / "exiftool.exe").exists():
        print(f"already present: {TARGET / 'exiftool.exe'}")
        return 0
    print(f"downloading ExifTool {EXIFTOOL_VERSION} ...")
    with urllib.request.urlopen(URL, timeout=120) as resp:
        blob = resp.read()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != SHA256:
        print(f"sha256 mismatch: expected {SHA256}, got {digest}")
        return 1
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        root = zf.namelist()[0].split("/")[0]
        tmp = REPO / "tools" / "_exiftool_tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        zf.extractall(tmp)
    shutil.rmtree(TARGET, ignore_errors=True)
    shutil.move(str(tmp / root), str(TARGET))
    shutil.rmtree(tmp, ignore_errors=True)
    (TARGET / "exiftool(-k).exe").rename(TARGET / "exiftool.exe")
    print(f"installed to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
