"""임무 경로 지도 (report/mission_map.py): 두 좌표계 figure, 아핀 연동, 공용 위성 배경 JS."""

from __future__ import annotations

from pathlib import Path

import pytest

from flightqa.core.mission import load_mission
from flightqa.report import html as html_report
from flightqa.report.mission_map import (
    basemap_options,
    build_figures,
    render_mission_map,
    utm_epsg_of,
    write_mission_map,
)
from test_mission import SQUARE, make_kmz


def test_build_figures_two_crs_and_affine(tmp_path: Path) -> None:
    wps = [(127.0, 37.0), (127.001, 37.0), (127.001, 37.001)]
    a = load_mission(make_kmz(tmp_path / "a.kmz", SQUARE, wps))
    b = load_mission(make_kmz(tmp_path / "b.kmz", [(lo + 0.01, la) for lo, la in SQUARE]))
    figs = build_figures([a, b], selected=1)
    assert figs["epsg"] == 32652 == utm_epsg_of([a, b])
    for crs in ("utm", "wgs84"):
        fig = figs[crs]
        meta = fig["layout"]["meta"]
        assert meta["crs"] == crs and len(meta["bounds"]) == 2 and len(meta["affine"]) == 6
        # 폴리곤 2 + 웨이포인트(선, 시작, 끝) 3 = 5 트레이스
        assert len(fig["data"]) == 5
        names = [t["name"] for t in fig["data"]]
        assert names[0] == "a" and "a 웨이포인트" in names and "b" in names
        # 선택한 임무는 진하게, 좌표가 hover 텍스트에 들어간다
        poly_b = next(t for t in fig["data"] if t["name"] == "b")
        assert poly_b["line"]["width"] > fig["data"][0]["line"]["width"]
        assert ("E " in fig["data"][0]["text"][0]) == (crs == "utm")
        assert fig["layout"]["dragmode"] == "pan"
    # UTM 축 제목에 EPSG, 위경도 축은 위도 보정 비율
    assert "EPSG:32652" in figs["utm"]["layout"]["xaxis"]["title"]["text"]
    assert figs["wgs84"]["layout"]["yaxis"]["scaleratio"] == 1  # EPSG:4326 정사각 격자
    assert figs["utm"]["layout"]["xaxis"]["ticklabelstandoff"] == 6  # 보고서와 같은 눈금 여백
    # 아핀: UTM 꼭짓점 → 위경도 복원 오차 1e-5° 미만
    aff = figs["utm"]["layout"]["meta"]["affine"]
    x, y = figs["utm"]["data"][0]["x"][0], figs["utm"]["data"][0]["y"][0]
    lon = aff[0] * x + aff[1] * y + aff[2]
    lat = aff[3] * x + aff[4] * y + aff[5]
    assert lon == pytest.approx(127.0, abs=1e-5) and lat == pytest.approx(37.0, abs=1e-5)
    # 웨이포인트 없는 임무의 bounds 는 폴리곤만으로
    assert figs["wgs84"]["layout"]["meta"]["bounds"][1] == pytest.approx(
        [127.01, 37.0, 127.011, 37.001]
    )
    assert build_figures([], -1)["epsg"] is None


def test_render_and_write(tmp_path: Path) -> None:
    a = load_mission(make_kmz(tmp_path / "a.kmz", SQUARE))
    html = render_mission_map([a], 0, report_cfg={"basemap_provider": "esri"}, theme="dark")
    assert html.startswith("<!DOCTYPE html>") and 'data-theme="dark"' in html
    assert "window.FQBasemap" in html and "window.__fq_select" in html
    assert "FQBasemap.loadWarped(" in html  # UTM 보기에서도 위성 배경 (재투영)
    assert 'data-crs="utm"' in html and 'data-crs="wgs84"' in html
    assert "arcgisonline" in html  # esri 타일 URL 이 옵션에
    empty = render_mission_map([], -1)
    assert "임무 파일을 불러오면" in empty
    p = write_mission_map([a], tmp_path / "out" / "m.html", 0)
    assert p.exists() and p.stat().st_size > 1_000_000  # plotly.js 인라인


def test_basemap_options() -> None:
    assert basemap_options(None)["url"].startswith("https://server.arcgisonline.com")
    assert basemap_options({"basemap_provider": "vworld"})["url"] == ""  # 키 없으면 비활성
    o = basemap_options({"basemap_provider": "custom", "basemap_url": "https://t/{z}/{x}/{y}"})
    assert o["url"] == "https://t/{z}/{x}/{y}" and o["maxZoom"] == 19


def test_report_uses_shared_basemap_js() -> None:
    """보고서 템플릿도 같은 FQBasemap 을 쓴다 (타일 로직 한 벌)."""
    tpl = html_report._env().get_template("report.html.j2")
    src = tpl.render.__self__.environment.loader.get_source(  # type: ignore[attr-defined]
        html_report._env(), "report.html.j2"
    )[0]
    assert "_basemap.js.j2" in src and "FQBasemap.load(" in src
    assert "FQBasemap.loadWarped(" in src  # 보고서 UTM 지도에도 위성 배경
    assert "function tileToLon" not in src
