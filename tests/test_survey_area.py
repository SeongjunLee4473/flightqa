"""궤적 기반 조사 구역 추정 (analysis/survey_area.py) 과 GIS 쓰기 (report/geo.py) 단위 테스트.

수치는 손계산과 대조한다 (CLAUDE.md §11): 120 m AGL RedEdge 커버리지 = 106.67 × 80 m,
여유 폭 = 0.5 × min(106.67, 80) = 40 m.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import tifffile
from shapely.geometry import Point, Polygon, box

from flightqa.config.schema import SurveyAreaConfig, load_config
from flightqa.core.analysis.overlap import analyze_overlap, footprints
from flightqa.core.analysis.survey_area import estimate_survey_area
from flightqa.core.models import CameraModel, CaptureRow, LineInfo, Status
from flightqa.report import geo

T0 = datetime(2026, 6, 1, 3, 0, tzinfo=UTC)
CAM = CameraModel(
    sensor_width_mm=4.8,
    sensor_height_mm=3.6,
    focal_length_mm=5.4,
    image_width_px=1280,
    image_height_px=960,
)


def _row(i: int, x: float, y: float, heading: float = 90.0, line: int | None = 0) -> CaptureRow:
    return CaptureRow(
        index=i,
        capture_id=str(i),
        flight_index=0,
        time_utc=T0 + timedelta(seconds=4 * i),
        lat=37.0 + y / 111_000.0,
        lon=127.0 + x / 88_000.0,
        x_m=x,
        y_m=y,
        alt_msl_m=None,
        agl_m=120.0,
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


def _line(idx: int, s: int, e: int, heading: float) -> LineInfo:
    return LineInfo(
        index=idx,
        start_capture=s,
        end_capture=e,
        n_captures=e - s + 1,
        heading_deg=heading,
        length_m=100,
        mean_agl_m=120,
        std_agl_m=0,
        mean_speed_mps=5,
    )


def _two_lines() -> tuple[list[CaptureRow], list[LineInfo]]:
    rows = [_row(i, 20.0 * i, 0.0) for i in range(6)] + [
        _row(6 + i, 100.0 - 20.0 * i, 40.0, heading=270.0, line=1) for i in range(6)
    ]
    return rows, [_line(0, 0, 5, 90.0), _line(1, 6, 11, 270.0)]


def _cfg_half() -> SurveyAreaConfig:
    """손계산용: 여유 폭 = 0.5 × min(106.67, 80) = 40 m (기본값은 0)."""
    return load_config().survey_area.model_copy(update={"margin_frac": 0.5})


def test_survey_area_default_margin_is_zero() -> None:
    rows, lines = _two_lines()
    cfg = load_config()
    assert cfg.survey_area.margin_frac == 0.0
    ov = analyze_overlap(rows, lines, CAM, cfg.overlap, 32652, 40.0)
    res = estimate_survey_area(
        rows, lines, [False] * len(rows), CAM, ov.union, cfg.survey_area, 32652
    )
    sa = res.area
    assert sa is not None and sa.margin_m == 0.0
    # 점 껍질 = 100 × 40 사각형 그대로
    assert sa.area_m2 == pytest.approx(100 * 40, rel=0.01)
    assert sa.fill_pct == pytest.approx(100.0)


def test_survey_area_two_lines_full_fill() -> None:
    rows, lines = _two_lines()
    cfg = load_config()
    ov = analyze_overlap(rows, lines, CAM, cfg.overlap, 32652, 40.0)
    res = estimate_survey_area(rows, lines, [False] * len(rows), CAM, ov.union, _cfg_half(), 32652)
    sa = res.area
    assert sa is not None and sa.method == "trajectory_hull" and sa.n_points == 12
    assert sa.margin_m == pytest.approx(40.0)
    # 점 껍질 100×40 사각형 + 둥근 40 m 버퍼: (100+80)(40+80) − 4·(40² − π·40²/4)
    expect = 180 * 120 - 4 * (1600 - math.pi * 1600 / 4)
    assert sa.area_m2 == pytest.approx(expect, rel=0.01)
    # 커버리지(x −40..140, y −53..93) 가 구역을 완전히 덮는다
    assert sa.fill_pct == pytest.approx(100.0, abs=0.2)
    assert sa.n_uncovered == 0 and sa.uncovered_geojson is None
    assert sa.geojson is not None and sa.geojson["type"] == "Polygon"
    assert sa.bbox is not None and sa.bbox[0] < sa.bbox[2] and sa.bbox[1] < sa.bbox[3]
    assert sa.rings_m and len(sa.rings_m[0]) > 8
    by = {m.key: m for m in res.metrics}
    assert by["overlap.survey_area"].value == pytest.approx(expect / 10_000.0, rel=0.01)
    assert by["overlap.survey_fill"].status is Status.PASS
    assert by["overlap.survey_fill"].next_action is None
    assert "추정" in by["overlap.survey_area"].reason


def test_survey_area_ring_has_uncovered_interior() -> None:
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
    lines = [_line(q, 16 * q, 16 * q + 15, [90.0, 0.0, 270.0, 180.0][q]) for q in range(4)]
    cfg = load_config()
    ov = analyze_overlap(ring, lines, CAM, cfg.overlap, 32652, None)
    res = estimate_survey_area(ring, lines, [False] * len(ring), CAM, ov.union, _cfg_half(), 32652)
    sa = res.area
    assert sa is not None
    # 안쪽 (300 − 106.67)² ≈ 37,000 m² 가 비어 있으므로 충족률 < 85 % → FAIL
    assert sa.fill_pct is not None and sa.fill_pct < cfg.survey_area.fill_fail_pct
    assert sa.n_uncovered >= 1 and sa.uncovered_area_m2 > 30_000
    assert sa.uncovered_geojson is not None and sa.uncovered_geojson["type"] == "MultiPolygon"
    by = {m.key: m for m in res.metrics}
    assert by["overlap.survey_fill"].status is Status.FAIL
    assert (
        by["overlap.survey_fill"].next_action and "미촬영" in by["overlap.survey_fill"].next_action
    )


def test_survey_area_excludes_ground_and_transit() -> None:
    rows, lines = _two_lines()
    cfg = load_config()
    ground = [False] * len(rows)
    ground[0] = True
    ov = analyze_overlap(rows, lines, CAM, cfg.overlap, 32652, 40.0)
    res = estimate_survey_area(rows, lines, ground, CAM, ov.union, _cfg_half(), 32652)
    assert res.area is not None and res.area.n_points == 11
    # 전환 구간 라인은 점에서 빠진다
    lines2 = [lines[0], lines[1].model_copy(update={"is_transit": True})]
    # 라인 1개만 남으면 여유 0 으로는 면이 없으므로 40 m 여유로 확인
    res2 = estimate_survey_area(rows, lines2, ground, CAM, ov.union, _cfg_half(), 32652)
    assert res2.area is not None and res2.area.n_points == 5


def test_survey_area_single_line_and_too_few() -> None:
    cfg = load_config()
    rows = [_row(i, 20.0 * i, 0.0) for i in range(6)]
    lines = [_line(0, 0, 5, 90.0)]
    ov = analyze_overlap(rows, lines, CAM, cfg.overlap, 32652, None)
    # 한 줄: 오목 껍질이 면이 아니므로 볼록 껍질(선) + 버퍼 → 폭 80 m 의 둥근 띠
    res = estimate_survey_area(rows, lines, [False] * 6, CAM, ov.union, _cfg_half(), 32652)
    assert res.area is not None
    assert res.area.area_m2 == pytest.approx(100 * 80 + math.pi * 40**2, rel=0.01)
    # 기본(여유 0) 이면 한 줄은 면이 없어 구역이 없다 (metrics 도 없음)
    none = estimate_survey_area(rows, lines, [False] * 6, CAM, ov.union, cfg.survey_area, 32652)
    assert none.area is None and none.metrics == []
    # 점 부족 → N/A
    few = estimate_survey_area(
        rows[:3], [_line(0, 0, 2, 90.0)], [False] * 3, CAM, None, cfg.survey_area, 32652
    )
    assert few.area is None
    assert few.metrics[0].key == "overlap.survey_fill" and few.metrics[0].status is Status.NA
    # 비활성화 → 아무것도 없음
    off = cfg.survey_area.model_copy(update={"enabled": False})
    assert estimate_survey_area(rows, lines, [False] * 6, CAM, ov.union, off, 32652).metrics == []
    # margin_m 지정
    fixed = cfg.survey_area.model_copy(update={"margin_m": 10.0})
    r3 = estimate_survey_area(rows, lines, [False] * 6, CAM, ov.union, fixed, 32652)
    assert r3.area is not None and r3.area.margin_m == 10.0
    # EPSG 없음 → GeoJSON 없이 UTM 링만
    r4 = estimate_survey_area(rows, lines, [False] * 6, CAM, ov.union, _cfg_half(), None)
    assert r4.area is not None and r4.area.geojson is None and r4.area.rings_m


# --------------------------------------------------------------------------- geo 쓰기


def test_gpkg_blob_roundtrip() -> None:
    poly = box(127.0, 37.0, 127.01, 37.01)
    blob = geo.gpkg_blob(poly)
    assert blob[:2] == b"GP" and blob[2] == 0 and blob[3] == 0b11
    minx, maxx, miny, maxy = np.frombuffer(blob[8:40], dtype="<f8")
    assert (minx, miny, maxx, maxy) == pytest.approx(poly.bounds)
    assert geo.parse_gpkg_blob(blob).equals(poly)
    with pytest.raises(ValueError):
        geo.parse_gpkg_blob(b"XX" + blob[2:])


def test_raster_grid_and_geotiff(tmp_path: Path) -> None:
    grid = geo.RasterGrid(0.0, 0.0, 100.0, 50.0, 2.0)
    assert (grid.cols, grid.rows) == (50, 25) and grid.n_pixels == 1250
    assert grid.minx == 0.0 and grid.maxy == 50.0
    (r0, r1, c0, c1), m = grid.mask(box(10, 10, 30, 20))
    # x 10..30 → 열 5..15, y 10..20 → 행 (50−20)/2=15 .. (50−10)/2=20
    assert (r0, r1, c0, c1) == (15, 20, 5, 15)
    assert m.shape == (5, 10) and m.all()
    # 격자 밖은 잘린다
    (r0, r1, c0, c1), m = grid.mask(box(-10, -10, 4, 4))
    assert (c0, c1, r0, r1) == (0, 2, 23, 25) and m.all()
    # 회전 사각형 (헤딩 45°, 106.67×80 m): 셀 중심 판정 셀 수 ≈ 면적 / 4 m² (격자가 넉넉할 때)
    fp = footprints([_row(0, 50.0, 25.0, heading=45.0)], [_line(0, 0, 0, 45.0)], CAM)
    assert len(fp) == 1
    big = geo.RasterGrid(-100.0, -100.0, 200.0, 150.0, 2.0)
    _, m2 = big.mask(fp[0][1])
    assert m2.sum() == pytest.approx(106.67 * 80 / 4, rel=0.02)
    # 작은 격자에서는 격자 밖이 잘려 셀 수가 줄어든다
    _, m3 = grid.mask(fp[0][1])
    assert 0 < m3.sum() < m2.sum()

    arr = np.zeros((grid.rows, grid.cols), dtype=np.uint16)
    arr[0, 0] = 7
    path = geo.write_geotiff(tmp_path / "t.tif", arr, grid, 32652, nodata=0)
    with tifffile.TiffFile(path) as tf:
        page = tf.pages[0]
        assert isinstance(page, tifffile.TiffPage) and page.is_geotiff
        tags = page.geotiff_tags or {}
        assert tags["ProjectedCSTypeGeoKey"] == 32652
        assert tags["GTRasterTypeGeoKey"] == 1
        assert tuple(tags["ModelPixelScale"]) == (2.0, 2.0, 0.0)
        assert tuple(tags["ModelTiepoint"])[3:5] == (0.0, 50.0)
        nd = page.tags.get("GDAL_NODATA")
        assert nd is not None and str(nd.value).strip() == "0"
        back = page.asarray()
    assert back.dtype == np.uint16 and back[0, 0] == 7 and back.sum() == 7
    # 기존 DEM 리더로도 열린다 (같은 태그 규약)
    from flightqa.core.analysis.dem import DemSampler

    dem = DemSampler(path)
    assert dem.rows == 25 and dem.cols == 50


def test_gpkg_tables_and_layers(tmp_path: Path) -> None:
    """RunReport 없이 레이어 테이블만: 빈 레이어도 gpkg_contents 에 등록된다."""
    from flightqa.core.models import RunReport

    rep = RunReport.model_validate(
        {
            "generated_at": T0.isoformat(),
            "flightqa_version": "t",
            "exiftool_version": None,
            "root": str(tmp_path),
            "sensor": "rededge_mx",
            "sensor_display": "x",
            "discovery": {"root": str(tmp_path), "sensor": "rededge_mx", "confidence": 1.0},
            "n_captures": 0,
            "excluded": [],
            "flights": [],
            "status": "N/A",
            "headline": "",
            "next_actions": [],
            "config": load_config().model_dump(mode="json"),
        }
    )
    p = geo.write_gpkg(rep, tmp_path / "e.gpkg")
    con = sqlite3.connect(p)
    try:
        assert con.execute("PRAGMA application_id").fetchone()[0] == geo.GPKG_APPLICATION_ID
        names = {r[0] for r in con.execute("SELECT table_name FROM gpkg_contents")}
        assert names == set(geo.LAYER_GEOM)
        srs = con.execute(
            "SELECT definition FROM gpkg_spatial_ref_sys WHERE srs_id=4326"
        ).fetchone()
        assert srs and srs[0].startswith("GEOGCS")
        assert con.execute("SELECT count(*) FROM captures").fetchone()[0] == 0
    finally:
        con.close()
    # 비어 있어도 KML·GeoJSON 은 문서로 나온다
    k = geo.write_kml(rep, tmp_path / "e.kml")
    assert k.read_text(encoding="utf-8").startswith("<?xml")
    assert geo.write_rasters(rep, tmp_path) == []


def test_footprints_reprojection_helper() -> None:
    rows, lines = _two_lines()
    fp = footprints(rows, lines, CAM)
    assert len(fp) == 12 and isinstance(fp[0][1], Polygon)
    # to_xy 를 주면 그 좌표계를 쓴다 (여기서는 항등 이동)
    fp2 = footprints(
        rows,
        lines,
        CAM,
        to_xy=lambda lon, lat: ((lon - 127.0) * 88_000.0, (lat - 37.0) * 111_000.0),
    )
    assert fp2[0][1].centroid.distance(Point(0.0, 0.0)) < 1e-6
