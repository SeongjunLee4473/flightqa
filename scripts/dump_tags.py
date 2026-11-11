"""Dump raw exiftool tags of sample captures into docs/tags_<sensor>.json.

Usage (from repo root):
    uv run python scripts/dump_tags.py --sensor rededge_mx FILE [FILE ...]
    uv run python scripts/dump_tags.py --sensor dji_m3m FILE [FILE ...]

Two files are written per sensor:
    docs/tags_<sensor>.json          exiftool -j -G1 -struct -a  (human-readable values)
    docs/tags_<sensor>_numeric.json  exiftool -j -G1 -struct -a -n (numeric values)

Privacy: real field data must never enter the repository (CLAUDE.md section 12).
Directory paths, GPS coordinates and serial numbers are replaced with
format-preserving placeholders before writing.  Everything else is kept verbatim
so that parsers can be written against the exact tag names and value formats.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"

# 그룹(-G1 접두)과 무관하게 태그 이름으로 판별한다.
SERIAL_NAMES = {
    "SerialNumber",
    "Serial",
    "CameraSerialNumber",
    "DroneSerialNumber",
    "DroneID",
    "SensorID",
    "FlightId",
}
GPS_TAG_RE = re.compile(r"^[A-Za-z0-9-]+:GPS(Latitude|Longitude|Position)$")
# Placeholder coordinates: generic Korean-peninsula degrees with zeroed minutes/seconds.
LAT_DMS = "37 deg 0' 0.00\""
LON_DMS = "127 deg 0' 0.00\""
LAT = 37.0
LON = 127.0


def find_exiftool() -> str:
    bundled = REPO / "tools" / "exiftool" / "exiftool.exe"
    if bundled.exists():
        return str(bundled)
    found = shutil.which("exiftool")
    if found is None:
        sys.exit("exiftool not found: run scripts/fetch_exiftool.py or install it on PATH")
    return found


def run_exiftool(exe: str, files: list[str], numeric: bool) -> list[dict[str, Any]]:
    """Run exiftool once for all files.

    File names are passed through a UTF-8 argfile (``-@``) together with
    ``-charset filename=utf8``.  Passing non-ASCII paths directly on argv fails on
    Windows because the Perl runtime receives them in the ANSI code page.
    """
    args = ["-charset", "filename=utf8", "-j", "-G1", "-struct", "-a"]
    if numeric:
        args.append("-n")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".args", delete=False) as fh:
        fh.write("\n".join([*args, *files]) + "\n")
        argfile = fh.name
    try:
        out = subprocess.run(
            [exe, "-@", argfile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    finally:
        Path(argfile).unlink(missing_ok=True)
    data: list[dict[str, Any]] = json.loads(out.stdout)
    return data


def redact_value(tag: str, value: Any, numeric: bool) -> Any:
    if tag.split(":", 1)[-1] in SERIAL_NAMES:
        return "<REDACTED>"
    m = GPS_TAG_RE.match(tag)
    if m:
        kind = m.group(1)
        if numeric:
            if kind == "Position":
                return f"{LAT} {LON}"
            return LAT if kind == "Latitude" else LON
        s = str(value)
        if kind == "Position":
            return f"{LAT_DMS} N, {LON_DMS} E"
        base = LAT_DMS if kind == "Latitude" else LON_DMS
        suffix = s[-2:] if re.search(r" [NSEW]$", s) else ""
        return base + suffix
    return value


def redact(record: dict[str, Any], numeric: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tag, value in record.items():
        if tag == "SourceFile":
            out[tag] = "<sample>/" + Path(str(value)).name
        elif tag.split(":", 1)[-1] == "Directory":
            out[tag] = "<sample>"
        elif tag == "ExifTool:Warning":
            continue
        else:
            out[tag] = redact_value(tag, value, numeric)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sensor", required=True, choices=["rededge_mx", "dji_m3m"])
    ap.add_argument("files", nargs="+")
    ns = ap.parse_args()
    exe = find_exiftool()
    DOCS.mkdir(exist_ok=True)
    for numeric, suffix in ((False, ""), (True, "_numeric")):
        records = [redact(r, numeric) for r in run_exiftool(exe, ns.files, numeric)]
        target = DOCS / f"tags_{ns.sensor}{suffix}.json"
        target.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        n_tags = sum(len(r) for r in records)
        print(f"wrote {target.relative_to(REPO)}  ({len(records)} files, {n_tags} tags)")


if __name__ == "__main__":
    main()
