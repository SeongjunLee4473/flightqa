from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.synth import BuildResult, make_tiff
from flightqa.core.metadata import (
    CancelledError,
    CancelToken,
    ExifToolPool,
    FileEntry,
    exiftool_version_of,
    json_to_raw,
    raw_to_json,
    slim_raw,
)


def test_slim_raw_drops_noise() -> None:
    raw = {
        "SourceFile": "a.tif",
        "IFD0:Make": "MicaSense",
        "IFD0:StripOffsets": "1 2 3",
        "ExifIFD:ThumbnailImage": "(Binary data 33589 bytes, use -b option to extract)",
        "XMP-Camera:BandName": "Blue",
        "MPF:PreviewImage": "(Binary data 1103694 bytes, use -b option to extract)",
        "APP7:AEDebugInfo": "x",
        "Composite:GPSLatitude": 37.0,
        "Composite:FOV": "48.8 deg",
        "System:FileSize": 100,
    }
    out = slim_raw(raw)
    assert set(out) == {
        "SourceFile",
        "IFD0:Make",
        "XMP-Camera:BandName",
        "Composite:GPSLatitude",
        "System:FileSize",
    }
    assert json_to_raw(raw_to_json(out)) == out


def test_file_entry_key(tmp_path: Path) -> None:
    p = tmp_path / "x.tif"
    p.write_bytes(b"II*\x00")
    e = FileEntry.from_path(p)
    assert e.size == 4 and len(e.key) == 40
    assert FileEntry(path=p, size=4, mtime_ns=e.mtime_ns).key == e.key
    assert FileEntry(path=p, size=5, mtime_ns=e.mtime_ns).key != e.key


def test_cancel_token() -> None:
    tok = CancelToken()
    tok.check()
    tok.cancel()
    assert tok.is_cancelled
    with pytest.raises(CancelledError):
        tok.check()


@pytest.mark.exiftool
def test_pool_extracts_all_files_in_order_independent(synth_m3: BuildResult) -> None:
    entries = [FileEntry.from_path(p) for p in synth_m3.files]
    progress: list[tuple[int, int]] = []
    with ExifToolPool(workers=3, chunk_size=7) as pool:
        out = dict(pool.extract(entries, on_progress=lambda d, t, _n: progress.append((d, t))))
    assert len(out) == len(entries)
    assert all("ExifTool:Error" not in raw for raw in out.values())
    assert all(raw.get("XMP-drone-dji:CaptureUUID") for raw in out.values())
    assert progress[-1] == (len(entries), len(entries))
    assert all(d <= t for d, t in progress)


@pytest.mark.exiftool
def test_pool_reports_broken_file(tmp_path: Path) -> None:
    good = make_tiff(tmp_path / "IMG_0000_1.tif", width=8, height=8)
    # 추출 도중 파일이 사라진 경우 (스캔 후 삭제/이동): exiftool 이 오류를 내고 나머지는 계속된다
    missing = tmp_path / "IMG_0000_2.tif"
    entries = [FileEntry.from_path(good), FileEntry(path=missing, size=1, mtime_ns=1)]
    with ExifToolPool(workers=1) as pool:
        out = {e.path.name: raw for e, raw in pool.extract(entries)}
    assert out["IMG_0000_1.tif"]["IFD0:ImageWidth"] == 8
    assert "ExifTool:Error" in out["IMG_0000_2.tif"]


@pytest.mark.exiftool
def test_pool_cancel(synth_m3: BuildResult) -> None:
    entries = [FileEntry.from_path(p) for p in synth_m3.files]
    tok = CancelToken()
    tok.cancel()
    with ExifToolPool(workers=2, chunk_size=5) as pool, pytest.raises(CancelledError):
        list(pool.extract(entries, cancel=tok))


@pytest.mark.exiftool
def test_pool_empty_and_version(exiftool_exe: Path) -> None:
    with ExifToolPool(exiftool_exe, workers=1) as pool:
        assert list(pool.extract([])) == []
    assert exiftool_version_of(exiftool_exe) is not None
