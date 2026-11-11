"""Metashape 처리 보고서 파서·판정·내보내기 (core/metashape.py) — 합성 텍스트와 합성 PDF 로."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from fixtures.metashape_text import make_pdf, ms_report_lines, rgb_report_lines
from fixtures.synth import BuildResult
from flightqa.config.schema import load_config
from flightqa.core.metashape import (
    MetashapeError,
    extract_figures,
    extract_lines,
    judge,
    load_report,
    location_profile,
    normalize_lines,
    parse_lines,
    to_rows,
)
from flightqa.core.models import Status
from flightqa.report import export


def test_parse_rgb_report() -> None:
    rep = parse_lines(normalize_lines(rgb_report_lines()), source="rgb.pdf", n_pages=7)
    assert rep.report_date == "07 October 2026" and rep.n_pages == 7
    assert (rep.n_images, rep.n_aligned, rep.camera_stations) == (98, 98, 98)
    assert rep.flying_altitude_m == 131.0 and rep.gsd_cm == 3.52 and rep.coverage_km2 == 0.222
    assert (
        rep.tie_points == 233_346 and rep.tie_points_total == 247_603 and rep.projections == 845_599
    )
    assert rep.reprojection_error_px == 1.08 and rep.rms_reprojection_px == pytest.approx(1.08411)
    assert rep.rms_reprojection_norm == pytest.approx(0.228409)
    assert rep.max_reprojection_px == pytest.approx(44.6458)
    assert rep.tie_point_multiplicity == pytest.approx(3.99345)
    assert rep.coordinate_system == "WGS 84 (EPSG::4326)" and rep.software_version == "2.3.1.22580"
    assert len(rep.cameras) == 1 and not rep.is_multispectral
    cam = rep.cameras[0]
    assert cam.name == "M3M (12.29mm)" and cam.band is None
    assert (cam.width_px, cam.height_px, cam.focal_mm, cam.pixel_um) == (5280, 3956, 12.29, 3.36)
    assert cam.precalibrated is True and cam.n_images == 98
    assert sorted(cam.coefficients) == ["Cx", "Cy", "F", "K1", "K2", "K3", "P1", "P2"]
    assert cam.coefficients["F"].value == 3733.4 and cam.coefficients["F"].error == 0.087
    assert cam.coefficients["K1"].error == pytest.approx(3.1e-05)
    # 상관 행렬: 8 계수 → 28 쌍, 머리글 순서대로
    assert len(cam.correlations) == 28
    assert cam.correlations["K1-K2"] == -0.95 and cam.correlations["K2-K3"] == -0.97
    assert cam.correlations["Cx-P1"] == 0.70 and cam.correlations["F-P2"] == -0.02
    assert cam.max_abs_correlation == ("K2-K3", -0.97)
    le = rep.location_error
    assert le is not None and le.unit == "cm"
    assert le.xy_m == pytest.approx(0.012673) and le.total_m == pytest.approx(0.0169617)
    assert rep.dem["resolution"] == "7.03 cm/pix" and rep.dem["point_density"] == "202 points/m²"
    assert rep.alignment["Accuracy"] == "High" and rep.alignment["Tie point limit"] == "10,000"
    assert rep.depth_maps["Quality"] == "High" and rep.depth_maps["Count"] == "98"
    assert (
        rep.orthomosaic["Blending mode"] == "Mosaic"
        and rep.orthomosaic["Colors"] == "3 bands, uint8"
    )
    assert rep.raster_transform is None and rep.gcp == []
    assert rep.system["Software name"] == "Agisoft Metashape Professional"
    assert "Camera locations and image overlap." in rep.figures
    assert rep.warnings == []


def test_parse_multispectral_report() -> None:
    rep = parse_lines(normalize_lines(ms_report_lines()), source="ms.pdf")
    assert rep.is_multispectral and rep.n_bands == 5
    assert (
        rep.n_images == 1540
        and rep.n_aligned == 1520
        and rep.aligned_pct == pytest.approx(98.7, abs=0.05)
    )
    bands = [c.band for c in rep.cameras]
    assert bands == ["Blue", "Green", "Red", "Red edge", "NIR"]
    # 잘린 이름 "Red edge (5…" 도 보정 페이지와 짝이 맞고 "Red" 가 삼키지 않는다
    re_cam = rep.cameras[3]
    assert re_cam.n_images == 308 and "F" in re_cam.coefficients
    assert rep.cameras[2].n_images == 308 and rep.cameras[2].coefficients[
        "F"
    ].value == pytest.approx(1448.37 + 2)
    # 고정 K3: 값은 있고 오차·상관 없음, 상관 열에서 빠진다 (7 계수 → 21 쌍)
    k3 = re_cam.coefficients["K3"]
    assert k3.error is None and k3.value == pytest.approx(-0.078)
    assert len(re_cam.correlations) == 21 and re_cam.correlations["Cx-P1"] == 0.89
    assert re_cam.max_abs_correlation == ("Cx-P1", 0.89)
    le = rep.location_error
    assert le is not None and le.unit == "m" and le.xy_m == pytest.approx(1.04124)
    assert rep.raster_transform is not None and rep.raster_transform.startswith("B1 / 32768")
    assert rep.orthomosaic["Colors"] == "5 bands, uint16" and rep.point_colors == "5 bands, uint16"


def test_pdf_roundtrip(tmp_path: Path) -> None:
    """진짜 PDF 로 추출 경로까지: 글꼴이 없는 글자는 '?' 가 되지만 수치·라벨은 그대로 읽힌다."""
    pdf = make_pdf(tmp_path / "rgb.pdf", rgb_report_lines())
    lines, n_pages = extract_lines(pdf)
    assert n_pages >= 3 and any("Number of images" in ln for ln in lines)
    rep = load_report(pdf)
    assert rep.source == "rgb.pdf" and rep.n_images == 98 and rep.gsd_cm == 3.52
    assert rep.cameras[0].coefficients["K1"].value == pytest.approx(-0.108282)
    assert extract_figures(pdf) == []  # 그림 없는 합성 PDF
    # Metashape 보고서가 아닌 PDF
    other = make_pdf(tmp_path / "other.pdf", ["Some other document", "Hello"])
    with pytest.raises(MetashapeError, match="Metashape"):
        load_report(other)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(MetashapeError):
        load_report(bad)


def test_judge_rgb_and_ms() -> None:
    cfg = load_config().metashape
    rgb = judge(parse_lines(normalize_lines(rgb_report_lines()), "rgb.pdf"), cfg)
    by = {m.key: m for g in rgb.groups for m in g.metrics}
    assert by["ms.aligned"].status is Status.PASS and by["ms.aligned"].gate
    assert by["ms.reproj_rms"].status is Status.WARN  # 1.084 > 1.0
    assert by["ms.reproj_max"].status is Status.WARN  # 44.6 > 30
    assert by["ms.calib_corr"].status is Status.WARN and by["ms.calib_corr"].value == 0.97
    assert by["ms.multiplicity"].status is Status.PASS
    assert location_profile(rgb.report, cfg) == "rtk"
    assert by["ms.loc_xy"].status is Status.PASS and by["ms.loc_xy"].unit == "m"
    assert "RTK" in (by["ms.loc_xy"].threshold or "")
    assert by["ms.gcp_control"].status is Status.NA
    assert "ms.reflectance" not in by  # RGB 는 반사율 항목 없음
    assert rgb.status is Status.WARN and rgb.headline == "조건부 사용 가능"
    assert len(rgb.next_actions) == 3

    ms = judge(parse_lines(normalize_lines(ms_report_lines()), "ms.pdf"), cfg)
    by = {m.key: m for g in ms.groups for m in g.metrics}
    assert by["ms.aligned"].status is Status.WARN and "미정렬 20장" in by["ms.aligned"].reason
    assert "캡처 308 × 5 밴드" in by["ms.aligned"].reason
    assert by["ms.reproj_rms"].status is Status.PASS and by["ms.calib_corr"].status is Status.PASS
    assert location_profile(ms.report, cfg) == "gps"
    assert by["ms.loc_xy"].status is Status.PASS and "카메라 GPS" in (
        by["ms.loc_xy"].threshold or ""
    )
    assert by["ms.reflectance"].status is Status.PASS
    assert by["ms.cameras"].value == 5
    # 정렬 성공률 95 % 미만이면 게이트 FAIL → 재처리 검토
    bad = judge(parse_lines(normalize_lines(ms_report_lines(aligned=1400)), "ms.pdf"), cfg)
    assert bad.status is Status.FAIL and bad.headline == "재처리 검토 필요"
    assert "영상 정렬 성공률" in bad.headline_detail
    # 위치 오차 기준: RTK 프로필을 강제하면 1 m 는 FAIL
    strict = cfg.model_copy(update={"location_profile": "rtk"})
    forced = judge(parse_lines(normalize_lines(ms_report_lines()), "ms.pdf"), strict)
    assert {m.key: m for g in forced.groups for m in g.metrics}["ms.loc_xy"].status is Status.FAIL


def test_export_rows(tmp_path: Path) -> None:
    cfg = load_config().metashape
    res = judge(parse_lines(normalize_lines(rgb_report_lines()), "rgb.pdf"), cfg)
    rows = to_rows(res)
    assert {"group", "key", "status", "reason"} <= set(rows[0]) and len(rows) >= 15
    written = export.write_metashape(res, tmp_path / "out", stem="rgb")
    assert [p.name for p in written] == ["rgb_metashape.json", "rgb_metashape.csv"]
    data = json.loads(written[0].read_text(encoding="utf-8"))
    assert data["status"] == "WARN" and data["report"]["n_images"] == 98
    assert data["report"]["cameras"][0]["correlations"]["K2-K3"] == -0.97
    with written[1].open(encoding="utf-8-sig") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == len(rows) and csv_rows[0]["key"] == "ms.aligned"


@pytest.mark.exiftool
def test_crosscheck_with_flight(synth_clean: BuildResult, tmp_path: Path) -> None:
    """비행 분석(RedEdge 합성 12캡처) 과 대조: 영상 수 = 캡처 × 5."""
    from flightqa.core import pipeline

    flight = pipeline.run(synth_clean.root, load_config(), use_cache=False, skip_pixels=True)
    cfg = load_config().metashape
    lines = ms_report_lines()
    rep = parse_lines(normalize_lines(lines), "ms.pdf")
    rep.n_images = flight.n_captures * 5  # 합성 비행에 맞춘 영상 수
    rep.n_aligned = rep.n_images
    rep.flying_altitude_m = 120.0
    res = judge(rep, cfg, flight)
    by = {m.key: m for g in res.groups for m in g.metrics}
    assert res.flight_root == str(flight.root)
    assert by["ms.x_images"].status is Status.PASS and by["ms.x_bands"].status is Status.PASS
    assert by["ms.x_altitude"].status in (Status.PASS, Status.WARN)
    assert by["ms.x_gsd"].status is not Status.NA  # 비행 GSD 가 있으므로 비교됨
    # 영상 수가 크게 다르면 항목은 FAIL, 전체는 WARN 까지만 (대조는 진단용)
    rep.n_images = rep.n_aligned = flight.n_captures * 5 + 40
    res2 = judge(rep, cfg, flight)
    assert {m.key: m for g in res2.groups for m in g.metrics}["ms.x_images"].status is Status.FAIL
    assert res2.status is Status.WARN


def test_cli_metashape(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from flightqa.cli import app

    pdf = make_pdf(tmp_path / "rgb.pdf", rgb_report_lines())
    r = CliRunner().invoke(app, ["metashape", str(pdf), "-o", str(tmp_path / "out")])
    assert r.exit_code == 1, r.output  # WARN
    assert (tmp_path / "out" / "rgb_metashape.json").exists()
    r2 = CliRunner().invoke(app, ["metashape", str(pdf), "--json"])
    assert r2.exit_code == 1
    data = json.loads(r2.output)
    assert data[0]["status"] == "WARN"
