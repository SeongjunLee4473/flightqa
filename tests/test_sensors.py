"""센서 플러그인: docs/tags_*_numeric.json (실제 샘플, 좌표 마스킹) 을 입력으로 파싱을 검증한다."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timezone
from pathlib import Path

import pytest

from flightqa.core.models import SensorKind
from flightqa.core.sensors import all_sensors, get_sensor
from flightqa.core.sensors.base import as_float, as_int, parse_exif_datetime, rad_to_deg
from flightqa.core.sensors.dji_m3m import DjiM3M
from flightqa.core.sensors.rededge_mx import RedEdgeMX

ROOT = Path("D:/data")


def _records(docs: list[dict[str, object]]) -> list[tuple[Path, dict[str, object]]]:
    return [(ROOT / "0000SET/000" / Path(str(r["SourceFile"])).name, r) for r in docs]


# --------------------------------------------------------------------------- 헬퍼


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1.0),
        ("+1.70", 1.7),
        ("-0.63134", -0.63134),
        ("1/500", 0.002),
        ("4800 4800 4800 4800", 4800.0),
        ([3.5, 1], 3.5),
        ("", None),
        (None, None),
        ("abc", None),
        (True, None),
    ],
)
def test_as_float(value: object, expected: float | None) -> None:
    assert (
        as_float(value) == pytest.approx(expected)
        if expected is not None
        else as_float(value) is None
    )


def test_as_int() -> None:
    assert as_int("50") == 50
    assert as_int(2.9) == 2
    assert as_int(None) is None


def test_rad_to_deg() -> None:
    assert rad_to_deg("0.8447652124996301") == pytest.approx(48.4, abs=0.05)


def test_parse_exif_datetime_variants() -> None:
    dt = parse_exif_datetime("2022:09:08 01:32:12", subsec="307911333", assume_tz=UTC)
    assert dt == datetime(2022, 9, 8, 1, 32, 12, 307911, tzinfo=UTC)
    dt2 = parse_exif_datetime("2026:09:07 06:37:59.539061")
    assert dt2 is not None and dt2.microsecond == 539061 and dt2.tzinfo is None
    dt3 = parse_exif_datetime("2026:09:07 15:37:39+09:00")
    assert dt3 is not None and dt3.utcoffset() == timezone(
        __import__("datetime").timedelta(hours=9)
    ).utcoffset(None)
    assert parse_exif_datetime("0000:00:00 00:00:00") is None
    assert parse_exif_datetime("garbage") is None
    assert parse_exif_datetime(None) is None


# --------------------------------------------------------------------------- RedEdge


def test_rededge_band_order_from_tags(docs_rededge: list[dict[str, object]]) -> None:
    """파일 번호 _4 = NIR, _5 = Red edge (docs/ASSUMPTIONS.md)."""
    s = RedEdgeMX()
    keys = [s.band_key(r, Path(str(r["SourceFile"]))) for r in docs_rededge]
    assert keys == ["blue", "green", "red", "nir", "rededge"]


def test_rededge_parse_sample(docs_rededge: list[dict[str, object]]) -> None:
    s = RedEdgeMX()
    recs = _records(docs_rededge)
    key = s.capture_key(recs[0][1], recs[0][0], ROOT)
    assert key == "spXF5CmeH2uM8LsFkvcs"
    m = s.parse(key or "", "0000SET/000", recs)
    assert m.sensor is SensorKind.REDEDGE_MX
    assert sorted(m.bands) == ["blue", "green", "nir", "red", "rededge"]
    assert m.missing_bands == []
    assert m.time_utc == datetime(2022, 9, 8, 1, 32, 12, 307911, tzinfo=UTC)
    assert m.lat == pytest.approx(37.0)
    assert m.lon == pytest.approx(127.0)
    assert m.alt_msl_m == pytest.approx(247.08)
    assert m.pressure_alt_m == pytest.approx(171.108, abs=0.01)
    assert m.yaw_deg == pytest.approx(math.degrees(-0.23765407515617154))
    assert m.solar_elevation_deg == pytest.approx(48.4, abs=0.05)
    assert m.solar_azimuth_deg == pytest.approx(math.degrees(2.3196764432448034))
    assert m.trigger == "2"
    assert m.model == "RedEdge-M"
    assert m.firmware == "v7.3.2"
    assert m.gps_xy_accuracy_m == pytest.approx(1.056, abs=0.001)
    assert m.rtk is None
    assert m.alt_rel_m is None
    b = m.bands["blue"]
    assert b.exposure.exposure_s == pytest.approx(1 / 5556, rel=1e-3)
    assert b.exposure.iso == 100
    assert b.irradiance == pytest.approx(89.794, abs=0.001)  # HorizontalIrradiance
    assert b.irradiance_raw == pytest.approx(112.1437, abs=0.001)
    assert b.black_level == 4800
    assert b.width_px == 1280 and b.height_px == 960 and b.bits == 16
    assert m.extras["dls_serial"] == "<REDACTED>"


def test_rededge_provisional_and_detect(tmp_path: Path) -> None:
    s = RedEdgeMX()
    root = tmp_path
    p = root / "0000SET" / "000" / "IMG_0012_4.tif"
    assert s.provisional(p, root) == ("0000SET/000/IMG_0012", "nir")
    assert s.provisional(root / "0000SET/000/IM1ABD~1.TIF", root) is None
    assert s.provisional(root / "0000SET/000/diag.dat", root) is None
    files = [root / f"0000SET/000/IMG_{i:04d}_{b}.tif" for i in range(4) for b in range(1, 6)]
    assert s.detect(files, root) == pytest.approx(1.0)
    assert DjiM3M().detect(files, root) == 0.0


def test_rededge_band_fallbacks() -> None:
    s = RedEdgeMX()
    assert s.band_key({"XMP-Camera:RigCameraIndex": 3}, Path("x.tif")) == "nir"
    assert s.band_key({}, Path("IMG_0001_5.tif")) == "rededge"
    assert s.band_key({}, Path("weird.tif")) is None
    assert s.band_by_name("Red edge") is not None and s.band_by_name("RedEdge") is not None


def test_rededge_missing_band_and_duplicate(docs_rededge: list[dict[str, object]]) -> None:
    s = RedEdgeMX()
    recs = [*_records(docs_rededge)[:3], _records(docs_rededge)[0]]  # blue 중복, nir/rededge 누락
    m = s.parse("k", "g", recs)
    assert m.missing_bands == ["nir", "rededge"]
    assert any("중복" in w for w in m.warnings)


# --------------------------------------------------------------------------- M3M


def test_m3m_parse_sample(docs_m3m: list[dict[str, object]]) -> None:
    s = DjiM3M()
    recs = [
        (Path("D:/data/DJI_202609071535_004_영역") / Path(str(r["SourceFile"])).name, r)
        for r in docs_m3m
    ]
    key = s.capture_key(recs[0][1], recs[0][0], Path("D:/data"))
    assert key == "c9027a6f52a94e38adc868f068a5147d"
    m = s.parse(key or "", "DJI_202609071535_004_영역", recs)
    assert sorted(m.bands) == ["green", "nir", "red", "rededge", "rgb"]
    assert m.missing_bands == []
    assert m.time_utc == datetime(2026, 9, 7, 6, 37, 59, 513176, tzinfo=UTC)
    assert m.utc_offset_min == 540
    assert m.alt_msl_m == pytest.approx(188.969)
    assert m.alt_rel_m == pytest.approx(53.642)
    assert m.gimbal_pitch_deg == pytest.approx(-89.9)
    assert m.yaw_deg == pytest.approx(-2.6)
    assert m.rtk is not None
    assert m.rtk.flag == 50 and m.rtk.fixed is True and m.rtk.status == "RTK"
    assert m.rtk.std_hgt_m == pytest.approx(0.0214)
    assert m.rtk.diff_age_s == pytest.approx(1.6)
    g = m.bands["green"]
    assert g.exposure.exposure_s == pytest.approx(0.002)
    assert g.exposure.iso == 170
    assert g.exposure.gain == pytest.approx(1.663)
    assert g.irradiance_raw == pytest.approx(11486.455)
    assert g.irradiance == pytest.approx(11486.455 / (0.182 * 64.0))
    assert g.black_level == 3200
    assert m.bands["rgb"].width_px == 5280
    assert m.extras["altitude_type"] == "RtkAlt"
    assert m.extras["flight_speed_mps"] == pytest.approx(0.1)


def test_m3m_band_key_paths(docs_m3m: list[dict[str, object]]) -> None:
    s = DjiM3M()
    jpg = next(r for r in docs_m3m if str(r["SourceFile"]).endswith(".JPG"))
    assert s.band_key(jpg, Path("x.JPG")) == "rgb"
    assert s.band_key({"XMP-drone-dji:ImageSource": "MS_RE_CAMERA"}, Path("x.TIF")) == "rededge"
    assert s.band_key({}, Path("DJI_20260907153739_0001_MS_NIR.TIF")) == "nir"
    assert s.band_key({}, Path("DJI_20260907153739_0001_D.JPG")) == "rgb"
    assert s.band_key({}, Path("other.TIF")) is None


def test_m3m_provisional(tmp_path: Path) -> None:
    s = DjiM3M()
    root = tmp_path
    p = root / "DJI_202609071535_004_영역-경로" / "DJI_20260907153739_0001_MS_G.TIF"
    assert s.provisional(p, root) == (
        "DJI_202609071535_004_영역-경로/DJI_20260907153739_0001",
        "green",
    )
    assert s.provisional(root / "m" / "DJI_20260907153739_0001_D.JPG", root)[1] == "rgb"  # type: ignore[index]
    assert s.provisional(root / "m" / "IMG_0001_1.tif", root) is None


def test_registry() -> None:
    kinds = [s.kind for s in all_sensors()]
    assert kinds == [SensorKind.REDEDGE_MX, SensorKind.DJI_M3M]
    assert get_sensor(SensorKind.DJI_M3M).display_name.startswith("DJI")
    with pytest.raises(KeyError):
        get_sensor("nope")  # type: ignore[arg-type]
