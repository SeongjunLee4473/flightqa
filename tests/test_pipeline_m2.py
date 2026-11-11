"""M2 파이프라인 통합: 합성 데이터에 주입한 포화·조도 급락·패널이 판정과 내보내기에 반영되는지."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from fixtures.synth import BuildResult
from flightqa.config.schema import load_config
from flightqa.core import pipeline
from flightqa.core.models import Metric, RunReport, Status
from flightqa.report import export


def _metric(report: RunReport, key: str, flight: int = 0) -> Metric:
    for g in report.flights[flight].groups:
        for m in g.metrics:
            if m.key == key:
                return m
    raise KeyError(key)


@pytest.fixture(scope="module")
def report_m2(synth_m2: BuildResult) -> RunReport:
    return pipeline.run(synth_m2.root, load_config(), use_cache=False)


@pytest.mark.exiftool
def test_groups_and_overlap(report_m2: RunReport) -> None:
    r = report_m2
    f = r.flights[0]
    assert [g.key for g in f.groups] == [
        "integrity",
        "flight",
        "overlap",
        "exposure",
        "irradiance",
        "panel",
    ]
    fwd = _metric(r, "overlap.forward")
    # 5 m/s × 3.8 s = 19 m, along-track 80 m → (80−19)/80 = 76.25 %
    assert fwd.value == pytest.approx(76.2, abs=0.2) and fwd.status is Status.PASS
    side = _metric(r, "overlap.side")
    assert side.value == pytest.approx(72.0, abs=1.0)  # (106.7 − 30)/106.7
    assert _metric(r, "overlap.gsd").value == pytest.approx(8.33, abs=0.01)
    assert f.coverage is not None and f.coverage.n_holes == 0 and f.coverage.area_m2 > 30_000
    assert f.coverage.geojson is not None
    rows = f.rows
    assert rows[3].forward_overlap_pct == pytest.approx(76.2, abs=0.2)
    assert rows[0].is_ground and rows[0].forward_overlap_pct is None


@pytest.mark.exiftool
def test_saturation_and_exposure(report_m2: RunReport, synth_m2: BuildResult) -> None:
    r = report_m2
    f = r.flights[0]
    sat = _metric(r, "exposure.saturated_captures")
    # 캡처 5 (2 %) 만 포화 캡처, 6 (0.1 %) 은 기준(0.5 %) 미만
    assert sat.details["indices"] == [5]
    n_air = synth_m2.n_captures - 2  # 지상 2 캡처 제외
    assert sat.value == pytest.approx(1 / n_air * 100, abs=0.1)
    assert sat.status is Status.PASS
    assert f.rows[5].sat_max_pct == pytest.approx(2.0, abs=0.2) and "saturated" in f.rows[5].flags
    assert f.rows[6].sat_max_pct == pytest.approx(0.1, abs=0.05)
    assert _metric(r, "exposure.underexposed").value == 0.0  # 텍스처 있는 합성 영상
    assert _metric(r, "exposure.jumps").value == 0
    assert len(f.pixel_rows) == synth_m2.n_captures * 5
    assert set(f.band_hist) == {"blue", "green", "red", "nir", "rededge"}
    assert all(sum(h) == synth_m2.n_captures * 64 * 48 for h in f.band_hist.values())
    assert len(f.hist_edges) == 65


@pytest.mark.exiftool
def test_irradiance_and_solar(report_m2: RunReport) -> None:
    r = report_m2
    ev = _metric(r, "irradiance.events")
    assert ev.value == 1 and ev.details["indices"] == [14] and ev.status is Status.WARN
    assert "irradiance_drop" in r.flights[0].rows[14].flags
    assert _metric(r, "irradiance.cv").status is Status.WARN
    sun = _metric(r, "irradiance.solar_elevation")
    assert sun.status is Status.PASS and sun.details["min_deg"] > 70  # 12:00 KST, 6월
    chk = _metric(r, "irradiance.xmp_solar_check")
    assert chk.status is Status.PASS and isinstance(chk.value, float) and chk.value < 0.5
    assert r.flights[0].rows[3].solar_elevation_calc_deg is not None


@pytest.mark.exiftool
def test_panel_detection(report_m2: RunReport) -> None:
    r = report_m2
    f = r.flights[0]
    assert f.panel is not None
    assert f.panel.pre_indices == [0, 1]
    assert f.panel.panel_like_indices == [0, 1]  # 합성 지상 캡처에 밝은 사각형
    pre = _metric(r, "panel.pre")
    assert pre.status is Status.PASS and pre.gate and "패널로 추정 2개" in pre.reason
    assert _metric(r, "panel.post").status is Status.WARN
    assert _metric(r, "panel.saturation").status is Status.PASS
    assert r.status is Status.WARN


@pytest.mark.exiftool
def test_m3m_panel_optional(synth_m3: BuildResult) -> None:
    r = pipeline.run(synth_m3.root, load_config(), use_cache=False)
    pre = _metric(r, "panel.pre", flight=1)
    assert pre.status is Status.NA and not pre.gate
    assert _metric(r, "panel.post", flight=1).status is Status.NA
    assert _metric(r, "irradiance.xmp_solar_check", flight=1).status is Status.NA
    assert r.flights[1].status is Status.PASS  # 두 번째 비행은 M2 항목 포함 전부 통과


@pytest.mark.exiftool
def test_skip_pixels_and_fast(synth_m2: BuildResult) -> None:
    cfg = load_config()
    r = pipeline.run(synth_m2.root, cfg, use_cache=False, skip_pixels=True)
    assert _metric(r, "exposure.saturated_captures").status is Status.NA
    assert r.flights[0].pixel_rows == []
    cfg.exposure.stride = 4
    r2 = pipeline.run(synth_m2.root, cfg, use_cache=False)
    assert r2.flights[0].pixel_rows[0].stride == 4
    # 64×48 을 4×4 로 샘플링하면 192 픽셀 → 픽셀당 0.52 % 단위라 오차가 큼
    assert r2.flights[0].rows[5].sat_max_pct == pytest.approx(2.0, abs=3.0)


@pytest.mark.exiftool
def test_pixel_cache_roundtrip(synth_m2: BuildResult, tmp_path: Path) -> None:
    cfg = load_config()
    cfg.cache.path = str(tmp_path / "c.sqlite")
    r1 = pipeline.run(synth_m2.root, cfg, use_cache=True)
    r2 = pipeline.run(synth_m2.root, cfg, use_cache=True)
    assert r2.timings_s["pixels"] < r1.timings_s["pixels"]
    assert r2.flights[0].pixel_rows == r1.flights[0].pixel_rows


@pytest.mark.exiftool
def test_m2_exports(report_m2: RunReport, tmp_path: Path) -> None:
    written = export.write_all(report_m2, tmp_path / "out")
    names = {p.name for p in written}
    assert {"saturation.csv", "coverage.geojson"} <= names
    with (tmp_path / "out" / "saturation.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == report_m2.n_captures * 5
    assert {"index", "band", "sat_pct", "stride", "flight_index"} <= set(rows[0])
    geo = json.loads((tmp_path / "out" / "coverage.geojson").read_text(encoding="utf-8"))
    kinds = [f["properties"]["kind"] for f in geo["features"]]
    assert kinds.count("coverage") == 1
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["flights"][0]["pixel_rows"] == []
    assert summary["flights"][0]["coverage"]["geojson"] is None
    assert summary["flights"][0]["band_hist"]["nir"]


@pytest.mark.exiftool
def test_m2_gis_exports(report_m2: RunReport, tmp_path: Path) -> None:
    """GeoPackage·KML·GeoTIFF·survey_area.geojson 이 QGIS 가 읽는 형식으로 나온다."""
    import sqlite3
    import xml.etree.ElementTree as ET

    import numpy as np
    import tifffile
    from pyproj import Transformer

    from flightqa.report import geo

    out = tmp_path / "gis"
    written = {p.name for p in export.write_all(report_m2, out)}
    assert set(export.EXPORT_FILES) <= written
    f = report_m2.flights[0]
    sa = f.survey_area
    assert sa is not None and sa.fill_pct is not None and sa.geojson is not None
    by = {m.key: m for g in f.groups for m in g.metrics}
    assert by["overlap.survey_area"].value and by["overlap.survey_fill"].value == sa.fill_pct
    assert sa.fill_pct >= 95.0  # 합성 3 라인 격자 비행은 구역을 다 덮는다

    # survey_area.geojson
    sj = json.loads((out / "survey_area.geojson").read_text(encoding="utf-8"))
    kinds = [x["properties"]["kind"] for x in sj["features"]]
    assert kinds.count("survey_area") == len(report_m2.flights)
    assert sj["features"][0]["properties"]["method"] == "trajectory_hull"

    # 가운데 라인의 캡처: 여유 0 인 추정 구역 안쪽 (바깥 라인은 경계 위)
    survey_lines = [ln for ln in f.lines if not ln.is_transit]
    mid_line = survey_lines[len(survey_lines) // 2]
    mid = f.rows[(mid_line.start_capture + mid_line.end_capture) // 2]
    # GeoPackage: 레이어·행 수·지오메트리 복원
    con = sqlite3.connect(out / "flightqa.gpkg")
    try:
        assert con.execute("PRAGMA application_id").fetchone()[0] == geo.GPKG_APPLICATION_ID
        rows = con.execute(
            "SELECT table_name, data_type, srs_id, min_x, max_x FROM gpkg_contents"
        ).fetchall()
        assert {r[0] for r in rows} == set(geo.LAYER_GEOM)
        assert all(r[1] == "features" and r[2] == 4326 for r in rows)
        n_pts = con.execute("SELECT count(*) FROM captures").fetchone()[0]
        assert n_pts == sum(1 for r in f.rows if r.lon is not None)
        blob, idx, flags = con.execute(
            "SELECT geom, idx, flags FROM captures ORDER BY fid LIMIT 1"
        ).fetchone()
        pt = geo.parse_gpkg_blob(blob)
        assert pt.geom_type == "Point" and idx == f.rows[0].index and flags == f.rows[0].flags
        (blob,) = con.execute("SELECT geom FROM survey_area").fetchone()
        poly = geo.parse_gpkg_blob(blob)
        assert poly.geom_type == "MultiPolygon"
        (mblob,) = con.execute("SELECT geom FROM captures WHERE idx=?", (mid.index,)).fetchone()
        assert poly.contains(geo.parse_gpkg_blob(mblob))  # 가운데 라인 캡처는 구역 안
        gtypes = dict(
            con.execute("SELECT table_name, geometry_type_name FROM gpkg_geometry_columns")
        )
        assert gtypes == geo.LAYER_GEOM
    finally:
        con.close()

    # KML: 비행 폴더 + 촬영 지점 Placemark
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    root = ET.parse(out / "flightqa.kml").getroot()
    folders = root.findall("./k:Document/k:Folder", ns)
    assert len(folders) == len(report_m2.flights)
    pms = folders[0].findall("./k:Folder/k:Placemark", ns)
    assert len(pms) == n_pts
    assert folders[0].find("./k:Placemark/k:MultiGeometry/k:Polygon", ns) is not None

    # GeoTIFF: 캡처 중심 셀은 1 장 이상이 덮고, 추정 구역 마스크는 그 자리를 2 로 표시
    assert f.coverage is not None and f.coverage.epsg is not None
    with tifffile.TiffFile(out / "overlap_count.tif") as tf:
        page = tf.pages[0]
        assert isinstance(page, tifffile.TiffPage) and page.is_geotiff
        tags = page.geotiff_tags or {}
        assert tags["ProjectedCSTypeGeoKey"] == f.coverage.epsg
        cell = float(tags["ModelPixelScale"][0])
        x0, y0 = float(tags["ModelTiepoint"][3]), float(tags["ModelTiepoint"][4])
        count = page.asarray()
    with tifffile.TiffFile(out / "survey_area.tif") as tf:
        mask = tf.pages[0].asarray()
    assert count.dtype == np.uint16 and mask.dtype == np.uint8 and count.shape == mask.shape
    assert count.max() >= 2  # 종중복이 있으니 2 장 이상 겹치는 셀이 있다
    tr = Transformer.from_crs(4326, f.coverage.epsg, always_xy=True)
    assert mid.lon is not None and mid.lat is not None
    x, y = tr.transform(mid.lon, mid.lat)
    c, r = int((x - x0) // cell), int((y0 - y) // cell)
    assert count[r, c] >= 1 and mask[r, c] == 2
    assert (mask == 0).any()  # 구역 밖 셀도 있다
    # 두 번째 실행에서도 같은 파일이 덮어써진다 (묵은 파일 없음)
    again = {p.name for p in export.write_all(report_m2, out)}
    assert again == written
