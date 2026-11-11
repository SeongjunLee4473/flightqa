"""비행별 임무 파일 매칭 (core/mission.match_mission), 폴리곤 면적, 임무 전용 GIS 내보내기."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from shapely.geometry import shape

from fixtures.synth import BuildResult
from flightqa.config.schema import load_config
from flightqa.core import pipeline
from flightqa.core.analysis.survey_area import METHOD_HULL, METHOD_MISSION
from flightqa.core.mission import load_mission, match_mission, polygon_area_m2
from flightqa.report import export, geo
from test_mission import SQUARE, make_kmz

FAR = [(128.0, 38.0), (128.001, 38.0), (128.001, 38.001), (128.0, 38.001)]
T0 = datetime(2026, 9, 29, 2, 20, tzinfo=UTC)


def _plans(tmp_path: Path) -> tuple[Path, Path]:
    a = make_kmz(tmp_path / "near.kmz", SQUARE)
    b = make_kmz(tmp_path / "far.kmz", FAR)
    return a, b


def test_match_by_polygon_coverage(tmp_path: Path) -> None:
    a, b = _plans(tmp_path)
    plans = [load_mission(b), load_mission(a)]  # 순서와 무관하게 겹치는 쪽
    pts = [(127.0002 + i * 0.0001, 37.0005) for i in range(8)]
    plan, frac = match_mission(plans, pts, tmp_path, T0, 50.0)
    assert plan is not None and plan.source == "near.kmz" and frac == 100.0
    # 절반만 안에 들어도 기준(50 %) 이상이면 채택, 미만이면 None
    half = pts[:4] + [(129.0, 39.0)] * 4
    assert match_mission(plans, half, tmp_path, T0, 50.0)[0] is not None
    plan, frac = match_mission(plans, half, tmp_path, T0, 60.0)
    assert plan is None and frac == 50.0
    # 아무것도 안 겹치면 None
    assert match_mission(plans, [(129.0, 39.0)], tmp_path, T0, 50.0) == (None, 0.0)
    assert match_mission([], pts, tmp_path, T0, 50.0) == (None, 0.0)


def test_match_tiebreak_name_then_time(tmp_path: Path) -> None:
    root = tmp_path / "site"
    (root / "DJI_202609291114_012_영역-경로-만들기19").mkdir(parents=True)
    same1 = load_mission(make_kmz(root / "영역-경로-만들기19.kmz", SQUARE))
    same2 = load_mission(make_kmz(root / "other.kmz", SQUARE))
    pts = [(127.0005, 37.0005)]
    # 같은 폴리곤 두 개 → 폴더명과 같은 이름 우선
    plan, _ = match_mission([same2, same1], pts, root, T0, 50.0)
    assert plan is not None and plan.source == "영역-경로-만들기19.kmz"
    # 이름 일치가 없으면 생성 시각이 비행 시작에 가까운 쪽
    older = same2.model_copy(update={"source": "old.kmz", "created_utc": T0 - timedelta(hours=5)})
    newer = same2.model_copy(update={"source": "new.kmz", "created_utc": T0 - timedelta(minutes=3)})
    plan, _ = match_mission([older, newer], pts, tmp_path, T0, 50.0)
    assert plan is not None and plan.source == "new.kmz"
    # 좌표가 없으면: 이름 일치 → 하나뿐이면 그것 → 아니면 None
    assert match_mission([same2, same1], [], root, T0, 50.0)[0] is same1
    assert match_mission([same2], [], tmp_path, T0, 50.0)[0] is same2
    assert match_mission([older, newer], [], tmp_path, T0, 50.0)[0] is None


def test_polygon_area(tmp_path: Path) -> None:
    plan = load_mission(make_kmz(tmp_path / "m.kmz", SQUARE))
    area = polygon_area_m2(plan)
    # 0.001° × 0.001° ≈ 88.9 m × 111.0 m ≈ 9,870 m² (위도 37°)
    assert area is not None and area == pytest.approx(9_870, rel=0.03)


def test_write_missions_gis(tmp_path: Path) -> None:
    a, b = _plans(tmp_path)
    plans = [load_mission(a), load_mission(b)]
    out = tmp_path / "gis"
    written = geo.write_missions_gis(plans, out)
    names = sorted(p.name for p in written)
    assert names == ["far.geojson", "far.gpkg", "missions.gpkg", "near.geojson", "near.gpkg"]
    con = sqlite3.connect(out / "missions.gpkg")
    try:
        assert con.execute("SELECT count(*) FROM mission_polygon").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM mission_waypoints").fetchone()[0] == 0
        (blob, src) = con.execute(
            "SELECT geom, source FROM mission_polygon ORDER BY fid LIMIT 1"
        ).fetchone()
        assert src == "near.kmz"
        assert geo.parse_gpkg_blob(blob).equals(shape(plans[0].polygon_geojson))
    finally:
        con.close()
    import json

    gj = json.loads((out / "near.geojson").read_text(encoding="utf-8"))
    assert gj["features"][0]["properties"]["kind"] == "mission_polygon"
    # 하나만 내보내면 합본은 없다
    assert not any(
        p.name == "missions.gpkg" for p in geo.write_missions_gis(plans[:1], tmp_path / "one")
    )


@pytest.mark.exiftool
def test_pipeline_picks_overlapping_mission_per_flight(
    synth_clean: BuildResult, tmp_path: Path
) -> None:
    """폴더에 임무 파일이 여러 개면 비행과 겹치는 것만 쓰고, 안 겹치는 파일은 무시한다."""
    import math
    import shutil

    from fixtures.synth import M_PER_DEG_LAT

    spec = synth_clean.spec

    def ll(x: float, y: float) -> tuple[float, float]:
        lat = spec.origin_lat + y / M_PER_DEG_LAT
        lon = spec.origin_lon + x / (M_PER_DEG_LAT * math.cos(math.radians(spec.origin_lat)))
        return lon, lat

    root = tmp_path / "site"
    for p in synth_clean.root.rglob("*"):
        if p.is_file():
            dst = root / p.relative_to(synth_clean.root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    make_kmz(root / "a_far.kmz", FAR)  # 이름순으로 먼저 오지만 겹치지 않는다
    make_kmz(root / "b_here.kmz", [ll(-20, -40), ll(115, -40), ll(115, 70), ll(-20, 70)])
    rep = pipeline.run(root, load_config(), use_cache=False, skip_pixels=True)
    sa = rep.flights[0].survey_area
    assert sa is not None and sa.method == METHOD_MISSION and sa.source == "b_here.kmz"
    assert rep.mission is not None and rep.mission.source == "b_here.kmz"
    assert [m.source for m in rep.missions] == ["b_here.kmz"]
    assert not any("궤적 추정을 씀" in w for w in rep.warnings)
    # 겹치는 파일이 없으면 경고 + 추정
    (root / "b_here.kmz").unlink()
    rep2 = pipeline.run(root, load_config(), use_cache=False, skip_pixels=True)
    assert rep2.flights[0].survey_area is not None
    assert rep2.flights[0].survey_area.method == METHOD_HULL
    assert rep2.missions == [] and rep2.mission is None
    assert any("겹치는 폴리곤이 없어" in w for w in rep2.warnings)
    # 내보내기의 임무 레이어는 실제로 쓰인 파일만
    out = tmp_path / "out"
    export.write_all(rep, out)
    con = sqlite3.connect(out / "flightqa.gpkg")
    try:
        rows = con.execute("SELECT source FROM mission_polygon").fetchall()
        assert rows == [("b_here.kmz",)]
    finally:
        con.close()
