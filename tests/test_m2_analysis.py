"""M2 분석 단위 테스트: 픽셀 통계, 중복도 기하, 노출, 조도·태양, 패널.

손계산 값과 대조한다 (CLAUDE.md §11).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile

from fixtures.synth import SATURATION_DN, make_tiff
from flightqa.config.schema import load_config
from flightqa.core.analysis.exposure import analyze_exposure
from flightqa.core.analysis.irradiance import analyze_irradiance, solar_positions
from flightqa.core.analysis.overlap import analyze_overlap, footprint_polygon
from flightqa.core.analysis.panel import analyze_panel, ground_flags
from flightqa.core.analysis.pixels import (
    PixelTask,
    compute_pixel_stats,
    pixel_cache_key,
    run_pixel_tasks,
)
from flightqa.core.metadata import CancelledError, CancelToken
from flightqa.core.models import (
    BandMeta,
    CameraModel,
    CaptureMeta,
    CaptureRow,
    ExposureInfo,
    LineInfo,
    SensorKind,
    Status,
)
from flightqa.core.sensors import get_sensor

T0 = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- pixels


def test_pixel_stats_saturation_and_hist(tmp_path: Path) -> None:
    p = make_tiff(tmp_path / "a.tif", width=200, height=100, fill=20000, saturated_fraction=0.05)
    st = compute_pixel_stats(PixelTask("k", str(p), SATURATION_DN, 1, 64))
    assert st["n"] == 20000
    assert st["sat_pct"] == pytest.approx(5.0, abs=0.01)
    assert st["max"] == SATURATION_DN
    assert sum(st["hist"]) == 20000 and len(st["hist"]) == 64
    assert st["hist"][-1] == 1000  # 포화 픽셀은 마지막 bin
    # stride 4 → 1/16 픽셀, 비율은 비슷
    st4 = compute_pixel_stats(PixelTask("k", str(p), SATURATION_DN, 4, 64))
    assert st4["n"] == 50 * 25
    assert st4["sat_pct"] == pytest.approx(5.0, abs=2.0)


def test_pixel_stats_bright_region(tmp_path: Path) -> None:
    p = make_tiff(tmp_path / "panel.tif", width=200, height=100, fill=20000, panel_rect=True)
    st = compute_pixel_stats(PixelTask("k", str(p), SATURATION_DN, 1, 64, bright_region=True))
    assert 0.05 <= st["bright_frac"] <= 0.15  # 사각형 면적 ≈ 9%
    assert st["bright_fill"] >= 0.9
    assert st["bright_cv"] < 0.05
    flat = make_tiff(tmp_path / "flat.tif", width=200, height=100, fill=20000)
    st2 = compute_pixel_stats(PixelTask("k", str(flat), SATURATION_DN, 1, 64, bright_region=True))
    assert st2["bright_fill"] < 0.6 or st2["bright_cv"] > 0.05


def test_pixel_stats_errors(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"II*\x00garbage")
    assert "error" in compute_pixel_stats(PixelTask("k", str(bad), SATURATION_DN))
    assert "error" in compute_pixel_stats(PixelTask("k", str(tmp_path / "missing.tif"), 1))
    assert pixel_cache_key("f", 65520, 1, 64, True) == "f|65520|1|64|1"


def test_run_pixel_tasks_sequential_and_cancel(tmp_path: Path) -> None:
    files = [make_tiff(tmp_path / f"{i}.tif", width=16, height=8, seed=i) for i in range(5)]
    tasks = [PixelTask(str(i), str(f), SATURATION_DN) for i, f in enumerate(files)]
    out = dict(run_pixel_tasks(tasks, workers=1))
    assert len(out) == 5 and all("sat_pct" in v for v in out.values())
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(CancelledError):
        list(run_pixel_tasks(tasks, workers=1, cancel=tok))


def test_make_tiff_texture_and_panel(tmp_path: Path) -> None:
    p = make_tiff(tmp_path / "t.tif", width=64, height=48, fill=20000, texture=0.3)
    img = tifffile.imread(p)
    assert img.dtype == np.uint16 and img.min() >= 14000 and img.max() <= 26000
    assert np.percentile(img, 99) - np.percentile(img, 1) > 0.05 * SATURATION_DN


# --------------------------------------------------------------------------- overlap


def _row(
    i: int,
    x: float,
    y: float,
    agl: float | None = 120.0,
    heading: float | None = 90.0,
    line: int | None = 0,
) -> CaptureRow:
    return CaptureRow(
        index=i,
        capture_id=str(i),
        flight_index=0,
        time_utc=T0 + timedelta(seconds=4 * i),
        lat=37.0,
        lon=127.0,
        x_m=x,
        y_m=y,
        alt_msl_m=None,
        agl_m=agl,
        speed_mps=5.0,
        heading_deg=heading,
        interval_s=4.0,
        line=line,
        off_nadir_deg=0.0,
        n_bands=5,
        missing_bands="",
        rtk_fixed=None,
        irradiance=None,
        solar_elevation_deg=None,
    )


def test_footprint_polygon_geometry() -> None:
    poly = footprint_polygon(
        0, 0, 90.0, 100.0, 80.0
    )  # 동쪽 진행: 폭 100 m 는 남북, 길이 80 m 는 동서
    minx, miny, maxx, maxy = poly.bounds
    assert (maxx - minx) == pytest.approx(80.0) and (maxy - miny) == pytest.approx(100.0)
    assert poly.area == pytest.approx(8000.0)


def test_overlap_numbers_and_holes() -> None:
    cam = CameraModel(
        sensor_width_mm=4.8,
        sensor_height_mm=3.6,
        focal_length_mm=5.4,
        image_width_px=1280,
        image_height_px=960,
    )
    cfg = load_config().overlap
    # 120 m AGL: 폭 106.67, 길이 80. 20 m 간격 → 종중복 75%; 라인 간격 40 m → 횡중복 62.5%
    rows = [_row(i, 20.0 * i, 0.0, line=0) for i in range(6)] + [
        _row(6 + i, 100.0 - 20.0 * i, 40.0, heading=270.0, line=1) for i in range(6)
    ]
    lines = [
        LineInfo(
            index=0,
            start_capture=0,
            end_capture=5,
            n_captures=6,
            heading_deg=90.0,
            length_m=100,
            mean_agl_m=120,
            std_agl_m=0,
            mean_speed_mps=5,
        ),
        LineInfo(
            index=1,
            start_capture=6,
            end_capture=11,
            n_captures=6,
            heading_deg=270.0,
            length_m=100,
            mean_agl_m=120,
            std_agl_m=0,
            mean_speed_mps=5,
        ),
    ]
    res = analyze_overlap(rows, lines, cam, cfg, 32652, 40.0)
    by = {m.key: m for m in res.group.metrics}
    assert res.forward_pct[1] == pytest.approx(75.0) and res.forward_pct[0] is None
    assert res.forward_pct[6] is None  # 라인 첫 캡처
    assert by["overlap.forward"].value == pytest.approx(75.0)
    assert by["overlap.forward"].status is Status.PASS and by["overlap.forward"].gate
    assert by["overlap.side"].value == pytest.approx(62.5, abs=0.1)
    assert by["overlap.side"].status is Status.PASS
    assert res.gsd_cm[0] == pytest.approx(8.33, abs=0.01)
    assert res.coverage is not None and res.coverage.n_holes == 0
    # 면적: 두 라인 합집합 ≈ (100+80) × (40+106.67) = 26,400 m²
    assert res.coverage.area_m2 == pytest.approx(180 * 146.67, rel=0.01)
    assert res.coverage.geojson is not None and res.coverage.geojson["type"] in (
        "Polygon",
        "MultiPolygon",
    )
    # 종중복 부족: 간격 70 m → (80-70)/80 = 12.5% → FAIL
    rows2 = [_row(i, 70.0 * i, 0.0) for i in range(5)]
    lines2 = [
        LineInfo(
            index=0,
            start_capture=0,
            end_capture=4,
            n_captures=5,
            heading_deg=90.0,
            length_m=280,
            mean_agl_m=120,
            std_agl_m=0,
            mean_speed_mps=5,
        )
    ]
    res2 = analyze_overlap(rows2, lines2, cam, cfg, 32652, None)
    by2 = {m.key: m for m in res2.group.metrics}
    assert by2["overlap.forward"].status is Status.FAIL
    assert by2["overlap.side"].status is Status.NA
    # 구멍: 라인 간격 200 m 인 두 라인 + 그 사이를 잇는 라인이 없음 → 합집합은 두 조각, 구멍은 아님
    # 구멍 케이스: 사각 고리 4 라인 (150 m 정사각형 둘레), 내부가 비어 있음
    ring: list[CaptureRow] = []
    k = 0
    for x0, y0, dx, dy, hd in [
        (0, 0, 1, 0, 90.0),
        (300, 0, 0, 1, 0.0),
        (300, 300, -1, 0, 270.0),
        (0, 300, 0, -1, 180.0),
    ]:
        for j in range(16):
            ring.append(_row(k, x0 + dx * 20.0 * j, y0 + dy * 20.0 * j, heading=hd, line=k // 16))
            k += 1
    rl = [
        LineInfo(
            index=q,
            start_capture=16 * q,
            end_capture=16 * q + 15,
            n_captures=16,
            heading_deg=[90.0, 0.0, 270.0, 180.0][q],
            length_m=300,
            mean_agl_m=120,
            std_agl_m=0,
            mean_speed_mps=5,
        )
        for q in range(4)
    ]
    res3 = analyze_overlap(ring, rl, cam, cfg, 32652, None)
    assert res3.coverage is not None and res3.coverage.n_holes == 1
    assert res3.coverage.holes_area_m2 > 10_000
    assert len(res3.coverage.hole_centroids) == 1
    by3 = {m.key: m for m in res3.group.metrics}
    assert by3["overlap.holes"].status is Status.WARN


def test_overlap_without_agl() -> None:
    cam = CameraModel(
        sensor_width_mm=4.8,
        sensor_height_mm=3.6,
        focal_length_mm=5.4,
        image_width_px=1280,
        image_height_px=960,
    )
    rows = [_row(i, 20.0 * i, 0.0, agl=None) for i in range(4)]
    res = analyze_overlap(rows, [], cam, load_config().overlap, 32652, None)
    by = {m.key: m for m in res.group.metrics}
    assert by["overlap.forward"].status is Status.NA and by["overlap.holes"].status is Status.NA
    assert res.coverage is None


# --------------------------------------------------------------------------- exposure


def _cap(
    i: int, exposure_s: float = 1 / 5000, irr: float = 90.0, solar: float | None = None
) -> CaptureMeta:
    c = CaptureMeta(
        capture_id=f"c{i}",
        sensor=SensorKind.REDEDGE_MX,
        index=i,
        time_utc=T0 + timedelta(seconds=4 * i),
        lat=37.0,
        lon=127.0,
        alt_msl_m=220.0,
        solar_elevation_deg=solar,
    )
    for b in ("blue", "green", "red", "nir", "rededge"):
        c.bands[b] = BandMeta(
            band=b,
            path=Path(f"{i}_{b}.tif"),
            exposure=ExposureInfo(exposure_s=exposure_s, iso=100),
            irradiance=irr,
        )
    return c


def _stats(
    n: int, sat: dict[int, float] | None = None, under: set[int] | None = None
) -> dict[tuple[int, str], dict[str, Any]]:
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for i in range(n):
        for b in ("blue", "green", "red", "nir", "rededge"):
            lo, hi = (20000.0, 20010.0) if under and i in under else (10000.0, 40000.0)
            out[(i, b)] = {
                "n": 100,
                "sat_pct": (sat or {}).get(i, 0.0),
                "p1": lo,
                "p99": hi,
                "mean": 25000.0,
                "hist": [1] * 8,
                "stride": 1,
            }
    return out


def test_exposure_saturation_and_jumps() -> None:
    cfg = load_config().exposure
    sensor = get_sensor(SensorKind.REDEDGE_MX)
    caps = [_cap(i) for i in range(20)]
    caps[7].bands["red"].exposure.exposure_s = 1 / 1000  # 5배 급변
    stats = _stats(20, sat={3: 1.0, 4: 6.0}, under={9})
    stats[(5, "nir")] = {"error": "boom"}
    res = analyze_exposure(caps, sensor, cfg, stats, [False] * 20)
    by = {m.key: m for m in res.group.metrics}
    assert by["exposure.saturated_captures"].value == pytest.approx(10.0)  # 2/20
    assert by["exposure.saturated_captures"].status is Status.WARN
    assert by["exposure.saturated_captures"].details["n_severe"] == 1
    assert res.saturated_indices == [3, 4]
    assert (
        by["exposure.underexposed"].value == pytest.approx(5.0)
        and by["exposure.underexposed"].status is Status.WARN
    )
    assert res.underexposed[9] is True and res.underexposed[0] is False
    assert by["exposure.jumps"].value == 2  # 6→7 과 7→8 (red)
    assert by["exposure.jumps"].status is Status.PASS
    assert by["exposure.read_errors"].value == 1
    assert len(res.pixel_rows) == 20 * 5 - 1
    assert res.band_hist["blue"] == [20] * 8 and len(res.hist_edges) == 9
    assert "1/5000s" in by["exposure.summary"].reason
    # 지상 캡처는 통계에서 제외
    res2 = analyze_exposure(caps, sensor, cfg, stats, [True] * 3 + [False] * 17)
    assert {m.key: m for m in res2.group.metrics}[
        "exposure.saturated_captures"
    ].value == pytest.approx(2 / 17 * 100, abs=0.01)


def test_exposure_no_stats() -> None:
    cfg = load_config().exposure
    res = analyze_exposure([_cap(0)], get_sensor(SensorKind.REDEDGE_MX), cfg, {}, [False])
    by = {m.key: m for m in res.group.metrics}
    assert by["exposure.saturated_captures"].status is Status.NA


# --------------------------------------------------------------------------- irradiance / solar


def test_solar_positions_against_sample_tag() -> None:
    """실제 샘플(docs/tags_rededge_mx): 2022-09-08 01:32:12Z, 37°N 127°E 근처.

    XMP SolarElevation 48.4°.
    """
    c = CaptureMeta(
        capture_id="x",
        sensor=SensorKind.REDEDGE_MX,
        time_utc=datetime(2022, 9, 8, 1, 32, 12, tzinfo=UTC),
        lat=37.75,
        lon=127.15,
    )
    el, az = solar_positions([c, CaptureMeta(capture_id="y", sensor=SensorKind.REDEDGE_MX)])
    assert el[0] == pytest.approx(48.4, abs=1.0) and az[0] == pytest.approx(133.0, abs=3.0)
    assert el[1] is None


def test_irradiance_events_and_cv() -> None:
    cfg = load_config().irradiance
    caps = [_cap(i, irr=90.0 if i not in (10, 11, 12) else 40.0, solar=73.5) for i in range(30)]
    res = analyze_irradiance(caps, cfg, list(range(30)), has_xmp_solar=True)
    by = {m.key: m for m in res.group.metrics}
    assert res.event_indices == [10]
    assert by["irradiance.events"].status is Status.WARN
    assert by["irradiance.cv"].value == pytest.approx(17.1, abs=0.5)  # 손계산: pstdev/mean
    assert by["irradiance.cv"].status is Status.WARN
    assert by["irradiance.stable_fraction"].value == pytest.approx(27 / 30 * 100)
    assert by["irradiance.symmetry"].value == pytest.approx(0.0)
    assert by["irradiance.solar_elevation"].status is Status.PASS  # 정오 KST, 74°
    assert by["irradiance.solar_elevation"].details["min_deg"] > 70
    mism = by["irradiance.xmp_solar_check"].value
    assert isinstance(mism, float) and mism < 1.0
    assert by["irradiance.xmp_solar_check"].status is Status.PASS


def test_irradiance_low_sun_and_mismatch() -> None:
    cfg = load_config().irradiance
    t = datetime(2026, 12, 21, 7, 30, tzinfo=UTC)  # 16:30 KST 동지 → 태양고도 낮음
    caps = []
    for i in range(10):
        c = _cap(i, solar=60.0)
        c.time_utc = t + timedelta(seconds=4 * i)
        caps.append(c)
    res = analyze_irradiance(caps, cfg, list(range(10)), has_xmp_solar=True)
    by = {m.key: m for m in res.group.metrics}
    assert by["irradiance.solar_elevation"].status is Status.FAIL
    assert by["irradiance.xmp_solar_check"].status is Status.WARN
    res2 = analyze_irradiance(caps, cfg, [], has_xmp_solar=False)
    assert {m.key: m for m in res2.group.metrics}["irradiance.xmp_solar_check"].status is Status.NA


def test_irradiance_no_tags() -> None:
    caps = [CaptureMeta(capture_id=str(i), sensor=SensorKind.DJI_M3M, index=i) for i in range(3)]
    res = analyze_irradiance(caps, load_config().irradiance, [0, 1, 2], has_xmp_solar=False)
    by = {m.key: m for m in res.group.metrics}
    assert by["irradiance.cv"].status is Status.NA and by["irradiance.events"].status is Status.NA
    assert by["irradiance.solar_elevation"].status is Status.NA


# --------------------------------------------------------------------------- panel


def test_ground_flags_and_panel() -> None:
    cfg = load_config()
    caps = [_cap(i) for i in range(12)]
    agl: list[float | None] = [0.5, 1.0, *([120.0] * 8), 2.0, 0.0]
    g = ground_flags(caps, agl, cfg.panel)
    assert g == [True, True] + [False] * 8 + [True, True]
    lines = [
        LineInfo(
            index=0,
            start_capture=2,
            end_capture=9,
            n_captures=8,
            heading_deg=90,
            length_m=100,
            mean_agl_m=120,
            std_agl_m=0,
            mean_speed_mps=5,
        )
    ]
    stats = _stats(12)
    for b in ("blue", "green", "red", "nir", "rededge"):
        stats[(0, b)].update({"bright_frac": 0.09, "bright_fill": 0.95, "bright_cv": 0.02})
        stats[(1, b)].update({"bright_frac": 0.09, "bright_fill": 0.95, "bright_cv": 0.02})
        stats[(11, b)]["sat_pct"] = 3.0
    group, info = analyze_panel(
        caps, g, lines, cfg.panel, cfg.exposure, stats, ["blue", "green", "red", "nir", "rededge"]
    )
    by = {m.key: m for m in group.metrics}
    assert info.pre_indices == [0, 1] and info.post_indices == [10, 11]
    assert info.panel_like_indices == [0, 1]
    assert info.saturated_indices == [11]
    assert by["panel.pre"].status is Status.PASS and by["panel.pre"].gate
    assert by["panel.post"].status is Status.PASS
    assert by["panel.saturation"].status is Status.FAIL and by["panel.saturation"].gate
    # 패널 없음 → FAIL 게이트 / required=false 면 WARN
    group2, _info2 = analyze_panel(caps, [False] * 12, lines, cfg.panel, cfg.exposure, {}, ["blue"])
    by2 = {m.key: m for m in group2.metrics}
    assert by2["panel.pre"].status is Status.FAIL and by2["panel.post"].status is Status.WARN
    assert by2["panel.saturation"].status is Status.NA
    # M3M 은 패널 선택 → 없어도 N/A, 게이트 아님
    group3, _ = analyze_panel(
        caps, [False] * 12, lines, cfg.panel, cfg.exposure, {}, ["blue"], SensorKind.DJI_M3M
    )
    by3 = {m.key: m for m in group3.metrics}
    assert by3["panel.pre"].status is Status.NA and by3["panel.post"].status is Status.NA
    assert not by3["panel.pre"].gate


def test_ground_flags_without_agl() -> None:
    cfg = load_config().panel
    caps = [_cap(i) for i in range(5)]
    caps[0].alt_msl_m = 100.0
    caps[1].alt_msl_m = 103.0
    assert ground_flags(caps, [None] * 5, cfg) == [True, True, False, False, False]
    assert ground_flags([], [], cfg) == []
    assert math.isfinite(1.0)
