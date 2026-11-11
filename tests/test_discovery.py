from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixtures.synth import BuildResult
from flightqa.core.discovery import (
    build_captures,
    detect_sensor,
    discover,
    regroup,
    scan_files,
    split_flights,
)
from flightqa.core.metadata import CancelledError, CancelToken, ExifToolPool
from flightqa.core.models import CaptureMeta, SensorKind
from flightqa.core.sensors import all_sensors, get_sensor


@pytest.mark.exiftool
def test_scan_excludes_corrupt_and_output_dir(synth_re: BuildResult) -> None:
    (synth_re.root / "flightqa_out").mkdir(exist_ok=True)
    (synth_re.root / "flightqa_out" / "IMG_9999_1.tif").write_bytes(b"II*\x00garbage")
    (synth_re.root / "0000SET" / "000" / "IMG_9998_1.tif").write_bytes(b"")
    entries, excluded = scan_files(synth_re.root)
    names = {e.path.name for e in entries}
    assert "IMG_9999_1.tif" not in names  # 출력 폴더는 스캔하지 않음
    reasons = {e.path.name: e.reason for e in excluded}
    assert "0 바이트" in reasons["IMG_9998_1.tif"]
    corrupt = synth_re.truth[7].files[0].name
    assert "TIFF 헤더 손상" in reasons[corrupt]
    (synth_re.root / "0000SET" / "000" / "IMG_9998_1.tif").unlink()


@pytest.mark.exiftool
def test_discover_rededge(synth_re: BuildResult) -> None:
    res, entries = discover(synth_re.root, all_sensors())
    assert res.sensor is SensorKind.REDEDGE_MX
    assert res.confidence > 0.9
    assert res.scores["dji_m3m"] == 0.0
    # 30 캡처 - 손상 1 파일은 잠정 캡처 수에 영향 없음, odd-name 캡처(15) 는 규칙 불일치
    assert res.n_provisional_captures == 29
    assert len(res.unresolved) == 10  # odd-name 5 + 복사본 5
    assert "0000SET/000" in res.groups
    assert len(entries) == len(synth_re.files) - 1


@pytest.mark.exiftool
def test_discover_m3m_korean_path(synth_m3: BuildResult) -> None:
    res, _entries = discover(synth_m3.root, all_sensors())
    assert res.sensor is SensorKind.DJI_M3M
    assert res.n_provisional_captures == 28
    assert all("합성" in g for g in res.groups)


def test_discover_empty(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x")
    res, entries = discover(tmp_path, all_sensors())
    assert res.sensor is None and entries == [] and res.n_files_scanned == 0
    assert detect_sensor([], tmp_path, all_sensors())[0] is None


@pytest.mark.exiftool
def test_regroup_reclaims_and_dedups(synth_re: BuildResult) -> None:
    sensor = get_sensor(SensorKind.REDEDGE_MX)
    res, entries = discover(synth_re.root, all_sensors())
    with ExifToolPool(workers=2, chunk_size=20) as pool:
        records = list(pool.extract(entries))
    g = regroup(sensor, res.root, records, set(res.unresolved))
    assert len(g.captures) == 30
    assert g.n_duplicates == 5
    assert g.n_reclaimed == 10
    assert all("중복" in e.reason for e in g.excluded)
    captures = build_captures(sensor, g)
    assert [c.index for c in captures] == list(range(30))
    assert captures[5].missing_bands == ["nir"]
    assert captures[7].missing_bands == ["blue"]  # 손상 파일은 스캔에서 제외됨
    assert captures[15].is_complete  # odd-name 파일은 태그로 편입
    times = [c.time_utc for c in captures]
    assert times == sorted(times)  # type: ignore[type-var]


def _cap(i: int, t: datetime | None) -> CaptureMeta:
    return CaptureMeta(capture_id=f"c{i}", sensor=SensorKind.REDEDGE_MX, index=i, time_utc=t)


def test_split_flights() -> None:
    t0 = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
    caps = [_cap(i, t0 + timedelta(seconds=4 * i)) for i in range(10)]
    caps += [_cap(10 + i, t0 + timedelta(minutes=30, seconds=4 * i)) for i in range(5)]
    caps.append(_cap(99, None))
    flights = split_flights(caps, gap_min=10.0)
    assert [len(f.captures) for f in flights] == [10, 5, 1]
    assert flights[0].flight_id.startswith("F1_20260601_010000Z")
    assert flights[2].flight_id == "untimed"
    assert flights[0].start_utc == t0 and flights[0].end_utc == t0 + timedelta(seconds=36)
    assert split_flights([], 10.0) == []


def test_scan_cancel(tmp_path: Path) -> None:
    (tmp_path / "IMG_0001_1.tif").write_bytes(b"II*\x00" + b"\x00" * 16)
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(CancelledError):
        scan_files(tmp_path, cancel=tok)
