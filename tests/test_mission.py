"""임무 파일(KMZ/WPML) 읽기 (core/mission.py) 와 임무 폴리곤 기반 조사 구역 판정.

합성 KMZ 는 docs/tags_dji_wpml.md 의 실제 구조를 그대로 흉내 낸다 (좌표만 합성).
"""

from __future__ import annotations

import math
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from shapely.geometry import box, shape

from fixtures.synth import BuildResult
from flightqa.config.schema import load_config
from flightqa.core import pipeline
from flightqa.core.analysis.overlap import analyze_overlap
from flightqa.core.analysis.survey_area import METHOD_HULL, METHOD_MISSION, estimate_survey_area
from flightqa.core.mission import (
    MissionError,
    choose_mission_file,
    find_mission_files,
    load_mission,
    mission_name_of_folder,
)
from flightqa.core.models import RunReport, Status
from flightqa.report import export, geo
from test_survey_area import CAM, _line, _row

WPML = "http://www.dji.com/wpmz/1.0.6"


def template_kml(polygon: list[tuple[float, float]], *, margin: float = 10.0) -> str:
    coords = "\n".join(f"{lon:.6f},{lat:.6f}" for lon, lat in polygon)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="{WPML}">
  <Document>
    <wpml:createTime>1790648226033</wpml:createTime>
    <wpml:missionConfig>
      <wpml:takeOffRefPoint>37.000100,127.000200,108.400000</wpml:takeOffRefPoint>
      <wpml:droneInfo><wpml:droneEnumValue>77</wpml:droneEnumValue></wpml:droneInfo>
      <wpml:payloadInfo><wpml:payloadEnumValue>68</wpml:payloadEnumValue></wpml:payloadInfo>
    </wpml:missionConfig>
    <Folder>
      <wpml:templateType>mapping2d</wpml:templateType>
      <wpml:waylineCoordinateSysParam>
        <wpml:heightMode>relativeToStartPoint</wpml:heightMode>
        <wpml:globalShootHeight>108.44</wpml:globalShootHeight>
      </wpml:waylineCoordinateSysParam>
      <wpml:autoFlightSpeed>6</wpml:autoFlightSpeed>
      <Placemark>
        <wpml:elevationOptimizeEnable>1</wpml:elevationOptimizeEnable>
        <wpml:shootType>time</wpml:shootType>
        <wpml:direction>90</wpml:direction>
        <wpml:margin>{margin:g}</wpml:margin>
        <wpml:overlap>
          <wpml:orthoCameraOverlapH>85</wpml:orthoCameraOverlapH>
          <wpml:orthoCameraOverlapW>80</wpml:orthoCameraOverlapW>
        </wpml:overlap>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>
{coords}
        </coordinates></LinearRing></outerBoundaryIs></Polygon>
        <wpml:height>108.44</wpml:height>
      </Placemark>
      <wpml:payloadParam><wpml:imageFormat>visable,narrow_band</wpml:imageFormat></wpml:payloadParam>
    </Folder>
  </Document>
</kml>
"""


def waylines_wpml(points: list[tuple[float, float]]) -> str:
    pms = "\n".join(
        f"<Placemark><Point><coordinates>{lon:.6f},{lat:.6f}</coordinates></Point>"
        f"<wpml:index>{i}</wpml:index></Placemark>"
        for i, (lon, lat) in enumerate(points)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:wpml="{WPML}">
  <Document><Folder>
    <wpml:distance>2799.03</wpml:distance><wpml:duration>475.3</wpml:duration>
    {pms}
  </Folder></Document>
</kml>
"""


def make_kmz(
    path: Path,
    polygon: list[tuple[float, float]],
    waypoints: list[tuple[float, float]] | None = None,
    *,
    margin: float = 10.0,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("wpmz/template.kml", template_kml(polygon, margin=margin))
        if waypoints is not None:
            zf.writestr("wpmz/waylines.wpml", waylines_wpml(waypoints))
    return path


SQUARE = [(127.0, 37.0), (127.001, 37.0), (127.001, 37.001), (127.0, 37.001)]


def test_load_mission_kmz(tmp_path: Path) -> None:
    p = make_kmz(tmp_path / "m.kmz", SQUARE, [(127.0, 37.0), (127.001, 37.0), (127.001, 37.0005)])
    mp = load_mission(p)
    assert mp.source == "m.kmz" and mp.wpml_version == "1.0.6"
    assert mp.template_type == "mapping2d" and mp.drone_enum == 77 and mp.payload_enum == 68
    assert mp.created_utc == datetime(2026, 9, 29, 2, 17, 6, 33000, tzinfo=UTC)
    assert mp.height_m == pytest.approx(108.44) and mp.height_mode == "relativeToStartPoint"
    assert mp.margin_m == 10.0 and (mp.overlap_h_pct, mp.overlap_w_pct) == (85, 80)
    assert mp.direction_deg == 90.0 and mp.speed_mps == 6.0 and mp.shoot_type == "time"
    assert mp.elevation_optimize and mp.image_format == "visable,narrow_band"
    assert mp.takeoff_ref == [37.0001, 127.0002, 108.4]  # lat, lon, alt 순서 유지
    poly = shape(mp.polygon_geojson)
    assert poly.geom_type == "Polygon" and poly.is_valid and mp.n_polygon_vertices == 4
    assert poly.equals(box(127.0, 37.0, 127.001, 37.001))
    assert mp.n_waypoints == 3 and mp.waypoints_geojson is not None
    assert mp.distance_m == pytest.approx(2799.03) and mp.duration_s == pytest.approx(475.3)


def test_load_mission_plain_kml_and_errors(tmp_path: Path) -> None:
    k = tmp_path / "plain.kml"
    k.write_text(template_kml(SQUARE), encoding="utf-8")
    mp = load_mission(k)
    assert mp.n_waypoints == 0 and mp.waypoints_geojson is None and mp.distance_m is None
    # 폴리곤 없음
    nop = tmp_path / "nopoly.kml"
    nop.write_text(template_kml(SQUARE).replace("<Polygon>", "<X>").replace("</Polygon>", "</X>"))
    with pytest.raises(MissionError, match="폴리곤"):
        load_mission(nop)
    # zip 아님 / 없음 / 깨진 XML
    bad = tmp_path / "bad.kmz"
    bad.write_bytes(b"not a zip")
    with pytest.raises(MissionError, match="zip"):
        load_mission(bad)
    with pytest.raises(MissionError, match="없음"):
        load_mission(tmp_path / "missing.kmz")
    broken = tmp_path / "broken.kml"
    broken.write_text("<kml><Document>", encoding="utf-8")
    with pytest.raises(MissionError, match="파싱"):
        load_mission(broken)
    # template.kml 없는 zip
    with zipfile.ZipFile(tmp_path / "empty.kmz", "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(MissionError, match=r"template\.kml"):
        load_mission(tmp_path / "empty.kmz")


def test_find_and_choose_mission_file(tmp_path: Path) -> None:
    root = tmp_path / "site"
    (root / "DJI_202609291114_012_영역-경로-만들기19").mkdir(parents=True)
    (root / "flightqa_out").mkdir()
    make_kmz(root / "other.kmz", SQUARE)
    make_kmz(root / "영역-경로-만들기19.kmz", SQUARE)
    make_kmz(root / "flightqa_out" / "ignored.kmz", SQUARE)
    (root / "deep" / "a" / "b" / "c").mkdir(parents=True)
    make_kmz(root / "deep" / "a" / "b" / "c" / "toodeep.kmz", SQUARE)
    files = find_mission_files(root)
    assert [f.name for f in files] == ["other.kmz", "영역-경로-만들기19.kmz"]
    assert (
        mission_name_of_folder(root / "DJI_202609291114_012_영역-경로-만들기19")
        == "영역-경로-만들기19"
    )
    assert mission_name_of_folder(root) is None
    chosen, warn = choose_mission_file(files, root)
    assert chosen is not None and chosen.name == "영역-경로-만들기19.kmz" and warn is None
    # 이름 일치가 없고 여러 개 → 첫 파일 + 경고
    chosen, warn = choose_mission_file([root / "a.kmz", root / "b.kmz"], tmp_path)
    assert chosen is not None and chosen.name == "a.kmz" and warn and "2개" in warn
    assert choose_mission_file([], root) == (None, None)
    # 미션 폴더 자체가 root 일 때도 이름으로 고른다
    sub = root / "DJI_202609291114_012_영역-경로-만들기19"
    assert choose_mission_file(files, sub)[0] == root / "영역-경로-만들기19.kmz"


def test_survey_area_with_mission_polygon() -> None:
    rows = [_row(i, 20.0 * i, 0.0) for i in range(6)] + [
        _row(6 + i, 100.0 - 20.0 * i, 40.0, heading=270.0, line=1) for i in range(6)
    ]
    lines = [_line(0, 0, 5, 90.0), _line(1, 6, 11, 270.0)]
    cfg = load_config()
    ov = analyze_overlap(rows, lines, CAM, cfg.overlap, 32652, 40.0)
    # 임무 폴리곤이 커버리지(x −40..140, y −53..93) 안에 들어가면 충족 100 %
    mission = box(-20.0, -40.0, 120.0, 80.0)
    res = estimate_survey_area(
        rows,
        lines,
        [False] * 12,
        CAM,
        ov.union,
        cfg.survey_area,
        32652,
        mission=mission,
        mission_source="m.kmz",
    )
    sa = res.area
    assert sa is not None and sa.method == METHOD_MISSION and sa.source == "m.kmz"
    assert sa.margin_m == 0.0 and sa.area_m2 == pytest.approx(140 * 120)
    assert sa.fill_pct == pytest.approx(100.0) and sa.n_outside == 0 and sa.outside_pct == 0.0
    by = {m.key: m for m in res.metrics}
    assert by["overlap.survey_area"].label.startswith("임무 구역")
    assert "m.kmz" in by["overlap.survey_area"].reason
    assert by["overlap.survey_fill"].status is Status.PASS
    assert by["overlap.survey_outside"].value == 0.0
    # 폴리곤이 커버리지 밖까지 크면 미촬영이 생기고 밖 캡처는 여전히 0
    big = box(-100.0, -100.0, 200.0, 150.0)
    res2 = estimate_survey_area(
        rows, lines, [False] * 12, CAM, ov.union, cfg.survey_area, 32652, mission=big
    )
    assert res2.area is not None and res2.area.fill_pct is not None
    assert res2.area.fill_pct == pytest.approx(180 * 146.67 / (300 * 250) * 100, rel=0.01)
    assert {m.key: m for m in res2.metrics}["overlap.survey_fill"].status is Status.FAIL
    # 폴리곤이 라인 끝을 잘라내면 밖 캡처가 생긴다 (x 30..70 → 라인당 x=40,60 두 개만 안)
    small = box(30.0, -40.0, 70.0, 80.0)
    res3 = estimate_survey_area(
        rows, lines, [False] * 12, CAM, ov.union, cfg.survey_area, 32652, mission=small
    )
    assert res3.area is not None and res3.area.n_outside == 8
    assert res3.area.outside_pct == pytest.approx(8 / 12 * 100, abs=0.1)
    # 점이 부족해도 임무 폴리곤이 있으면 구역은 나온다
    res4 = estimate_survey_area(
        rows[:2],
        [_line(0, 0, 1, 90.0)],
        [False] * 2,
        CAM,
        None,
        cfg.survey_area,
        32652,
        mission=mission,
    )
    assert res4.area is not None and res4.area.fill_pct is None and res4.area.n_points == 2
    # 빈 폴리곤이면 추정으로 돌아간다
    res5 = estimate_survey_area(
        rows, lines, [False] * 12, CAM, ov.union, cfg.survey_area, 32652, mission=box(0, 0, 0, 0)
    )
    assert res5.area is not None and res5.area.method == METHOD_HULL


@pytest.mark.exiftool
def test_pipeline_with_mission_file(synth_clean: BuildResult, tmp_path: Path) -> None:
    """--mission 으로 준 KMZ 폴리곤이 조사 구역이 되고 GIS 레이어로 나간다."""
    spec = synth_clean.spec
    from fixtures.synth import M_PER_DEG_LAT

    def ll(x: float, y: float) -> tuple[float, float]:
        lat = spec.origin_lat + y / M_PER_DEG_LAT
        lon = spec.origin_lon + x / (M_PER_DEG_LAT * math.cos(math.radians(spec.origin_lat)))
        return lon, lat

    # 합성 비행: 2 라인 × 6 캡처, 19 m 간격 → x 0..95, y 0..30.
    # 폴리곤은 라인 끝을 10 m 안쪽에서 자른다
    poly = [ll(10, -30), ll(85, -30), ll(85, 60), ll(10, 60)]
    kmz = make_kmz(tmp_path / "임무.kmz", poly, [ll(0, 0), ll(95, 0), ll(95, 30), ll(0, 30)])
    cfg = load_config()
    cfg.survey_area.mission_path = str(kmz)
    rep = pipeline.run(synth_clean.root, cfg, use_cache=False, skip_pixels=True)
    assert rep.mission is not None and rep.mission.source == "임무.kmz"
    f = rep.flights[0]
    sa = f.survey_area
    assert sa is not None and sa.method == METHOD_MISSION and sa.source == "임무.kmz"
    assert sa.area_m2 == pytest.approx(75 * 90, rel=0.01)
    # 라인 끝(x=0, 95) 캡처는 폴리곤 밖: 라인당 2개 이상 (전환 구간 판정에 따라 1–2개 더)
    assert sa.n_outside is not None and 4 <= sa.n_outside <= 6
    assert sa.fill_pct == pytest.approx(100.0, abs=0.5)
    by = {m.key: m for g in f.groups for m in g.metrics}
    assert by["overlap.survey_outside"].value == pytest.approx(
        sa.n_outside / sa.n_points * 100, abs=0.1
    )
    # 왕복 JSON (summary.json 과 같은 경로: 전환 라인의 heading NaN 은 mode="json" 덤프로만 돈다)
    again = RunReport.model_validate(rep.model_dump(mode="json"))
    assert again.mission is not None and again.mission.n_waypoints == 4

    out = tmp_path / "out"
    export.write_all(rep, out)
    con = sqlite3.connect(out / "flightqa.gpkg")
    try:
        assert con.execute("SELECT count(*) FROM mission_polygon").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM mission_waypoints").fetchone()[0] == 1
        (src, method) = con.execute("SELECT source, method FROM survey_area").fetchone()
        assert src == "임무.kmz" and method == METHOD_MISSION
        (blob,) = con.execute("SELECT geom FROM mission_polygon").fetchone()
        assert geo.parse_gpkg_blob(blob).geom_type == "MultiPolygon"
    finally:
        con.close()
    kml = (out / "flightqa.kml").read_text(encoding="utf-8")
    assert "mission_polygon" in kml and "mission_waypoints" in kml
    sj = (out / "survey_area.geojson").read_text(encoding="utf-8")
    assert '"kind": "mission_polygon"' in sj or '"kind":"mission_polygon"' in sj
    html = (
        (out / "report.html").read_text(encoding="utf-8") if (out / "report.html").exists() else ""
    )
    assert html == ""  # write_all 은 보고서를 쓰지 않는다

    # 자동 탐색: 폴더 안에 KMZ 를 두면 설정 없이도 읽는다 (읽기 실패는 경고)
    root2 = tmp_path / "auto"
    root2.mkdir()
    import shutil

    for p in synth_clean.root.rglob("*"):
        if p.is_file():
            dst = root2 / p.relative_to(synth_clean.root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    (root2 / "bad.kmz").write_bytes(b"nope")
    rep2 = pipeline.run(root2, load_config(), use_cache=False, skip_pixels=True)
    assert rep2.mission is None
    assert any("임무 파일을 읽지 못해" in w for w in rep2.warnings)
    assert rep2.flights[0].survey_area is not None
    assert rep2.flights[0].survey_area.method == METHOD_HULL
