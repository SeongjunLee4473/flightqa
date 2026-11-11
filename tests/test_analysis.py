"""무결성·궤적 분석: 손으로 계산할 수 있는 in-memory 캡처로 수치를 대조한다 (CLAUDE.md §11)."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from flightqa.config.schema import load_config
from flightqa.core.analysis.integrity import analyze_files, analyze_integrity, intervals_s
from flightqa.core.analysis.trajectory import analyze_trajectory as traj
from flightqa.core.analysis.trajectory import (
    ang_diff,
    circ_mean,
    heading_deg,
    off_nadir_deg,
    project,
    utm_epsg,
)
from flightqa.core.models import (
    BandMeta,
    CaptureMeta,
    DiscoveryResult,
    ExcludedFile,
    MissionPlan,
    RtkInfo,
    SensorKind,
    Status,
)
from flightqa.core.sensors import get_sensor

T0 = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
LAT0, LON0 = 37.0, 127.0
M_PER_DEG = 111_320.0


def _cap(
    i: int,
    x_m: float,
    y_m: float,
    *,
    alt: float = 220.0,
    dt_s: float | None = None,
    pitch: float = 0.0,
    roll: float = 0.0,
    sensor: SensorKind = SensorKind.REDEDGE_MX,
    rtk_flag: int | None = None,
    alt_rel: float | None = None,
) -> CaptureMeta:
    t = T0 + timedelta(seconds=dt_s if dt_s is not None else 4.0 * i)
    lat = LAT0 + y_m / M_PER_DEG
    lon = LON0 + x_m / (M_PER_DEG * math.cos(math.radians(LAT0)))
    m = CaptureMeta(
        capture_id=f"c{i}",
        sensor=sensor,
        index=i,
        time_utc=t,
        lat=lat,
        lon=lon,
        alt_msl_m=alt,
        alt_rel_m=alt_rel,
        pitch_deg=pitch,
        roll_deg=roll,
        rtk=RtkInfo(flag=rtk_flag, fixed=rtk_flag == 50) if rtk_flag is not None else None,
    )
    for b in ("blue", "green", "red", "nir", "rededge"):
        m.bands[b] = BandMeta(band=b, path=Path(f"{i}_{b}.tif"), time_utc=t, mtime_utc=t)
    return m


def _grid(
    n_lines: int = 2, per_line: int = 6, step: float = 20.0, spacing: float = 30.0
) -> list[CaptureMeta]:
    """동서 왕복 라인, 4 s 간격, step m 씩 → 5 m/s. 라인 사이 전환 캡처 1 개."""
    caps: list[CaptureMeta] = []
    i = 0
    for ln in range(n_lines):
        xs = [k * step for k in range(per_line)]
        if ln % 2:
            xs = xs[::-1]
        for x in xs:
            caps.append(_cap(i, x, ln * spacing))
            i += 1
        if ln < n_lines - 1:
            caps.append(_cap(i, xs[-1], ln * spacing + spacing / 2))
            i += 1
    return caps


# --------------------------------------------------------------------------- 기하 헬퍼


def test_heading_and_angles() -> None:
    assert heading_deg(1, 0) == pytest.approx(90)
    assert heading_deg(0, 1) == pytest.approx(0)
    assert heading_deg(-1, 0) == pytest.approx(270)
    assert ang_diff(350, 10) == pytest.approx(20)
    assert circ_mean([350, 10]) == pytest.approx(0, abs=1e-6) or circ_mean(
        [350, 10]
    ) == pytest.approx(360)
    assert math.isnan(circ_mean([]))
    assert utm_epsg(37.0, 127.0) == 32652
    assert utm_epsg(-33.0, 151.0) == 32756


def test_project_matches_haversine() -> None:
    a, b = _cap(0, 0, 0), _cap(1, 300, 400)
    xs, ys, epsg = project([a, b])
    assert epsg == 32652
    d_utm = math.hypot(xs[1] - xs[0], ys[1] - ys[0])
    # 독립 계산: haversine
    r = 6_371_000.0
    assert a.lat is not None and b.lat is not None and a.lon is not None and b.lon is not None
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d_hav = 2 * r * math.asin(math.sqrt(h))
    assert d_utm == pytest.approx(d_hav, rel=2e-3)
    assert d_utm == pytest.approx(500.0, rel=5e-3)


def test_off_nadir() -> None:
    c = CaptureMeta(
        capture_id="x", sensor=SensorKind.DJI_M3M, gimbal_pitch_deg=-85.0, gimbal_roll_deg=0.0
    )
    assert off_nadir_deg(c) == pytest.approx(5.0)
    flipped = CaptureMeta(
        capture_id="f", sensor=SensorKind.DJI_M3M, gimbal_pitch_deg=-90.0, gimbal_roll_deg=180.0
    )
    assert off_nadir_deg(flipped) == pytest.approx(0.0)  # 요 180° 뒤집힘 표기, 실제로는 연직
    c2 = CaptureMeta(capture_id="y", sensor=SensorKind.REDEDGE_MX, pitch_deg=3.0, roll_deg=4.0)
    assert off_nadir_deg(c2) == pytest.approx(5.0)
    assert math.isnan(off_nadir_deg(CaptureMeta(capture_id="z", sensor=SensorKind.REDEDGE_MX)))


# --------------------------------------------------------------------------- 무결성


def test_intervals_gaps_and_reversal() -> None:
    cfg = load_config().integrity
    caps = [_cap(i, i * 20.0, 0) for i in range(10)]
    caps[5].time_utc = T0 + timedelta(seconds=4 * 5 + 30)  # 공백 30 s
    for j in range(6, 10):
        caps[j].time_utc = T0 + timedelta(seconds=4 * j + 30)
    t7 = T0 + timedelta(seconds=4 * 7 + 30)
    caps[8].time_utc = t7 - timedelta(seconds=1)  # 역행
    caps[9].time_utc = t7 + timedelta(seconds=3)
    iv = intervals_s(caps)
    assert iv[0] is None and iv[1] == pytest.approx(4.0) and iv[5] == pytest.approx(34.0)
    group, _ivals, gaps = analyze_integrity(caps, get_sensor(SensorKind.REDEDGE_MX), cfg, 540)
    by = {m.key: m for m in group.metrics}
    assert gaps == [5]
    assert by["integrity.gaps"].status is Status.WARN
    assert (
        by["integrity.clock_reversal"].value == 1
        and by["integrity.clock_reversal"].status is Status.WARN
    )
    assert by["integrity.missing_bands"].status is Status.PASS
    assert by["integrity.interval_median"].value == pytest.approx(4.0)


def test_time_mismatch_with_utc_offset() -> None:
    cfg = load_config().integrity
    caps = [_cap(i, i * 20.0, 0) for i in range(4)]
    # RedEdge 관례: mtime 이 UTC−9h 로 보임 → 불일치 아님
    for c in caps:
        for b in c.bands.values():
            assert b.time_utc is not None
            b.mtime_utc = b.time_utc - timedelta(hours=9)
    group, _, _ = analyze_integrity(caps, get_sensor(SensorKind.REDEDGE_MX), cfg, 540)
    tm = next(m for m in group.metrics if m.key == "integrity.time_mismatch")
    assert tm.value == 0.0
    # 진짜 불일치: +3 h
    for b in caps[0].bands.values():
        assert b.time_utc is not None
        b.mtime_utc = b.time_utc + timedelta(hours=3)
    group, _, _ = analyze_integrity(caps, get_sensor(SensorKind.REDEDGE_MX), cfg, 540)
    tm = next(m for m in group.metrics if m.key == "integrity.time_mismatch")
    assert tm.value == pytest.approx(25.0) and tm.status is Status.WARN


def test_integrity_empty_and_missing() -> None:
    cfg = load_config().integrity
    group, _, _ = analyze_integrity([], get_sensor(SensorKind.REDEDGE_MX), cfg, None)
    by = {m.key: m for m in group.metrics}
    assert (
        by["integrity.gaps"].status is Status.NA
        and by["integrity.missing_bands"].status is Status.NA
    )
    caps = [_cap(i, i * 20.0, 0) for i in range(10)]
    del caps[0].bands["nir"]
    caps[0].missing_bands = ["nir"]
    group, _, _ = analyze_integrity(caps, get_sensor(SensorKind.REDEDGE_MX), cfg, None)
    mb = next(m for m in group.metrics if m.key == "integrity.missing_bands")
    assert mb.value == 10.0 and mb.status is Status.FAIL and mb.next_action


def test_analyze_files() -> None:
    cfg = load_config().integrity
    disc = DiscoveryResult(root=Path(), sensor=None, confidence=1.0, n_files_scanned=100)
    disc.ignored = [ExcludedFile(path=Path("a.tif"), reason="TIFF 헤더 손상")]
    g = analyze_files(disc, [ExcludedFile(path=Path("b.tif"), reason="중복: ...")], 1, 0, cfg)
    by = {m.key: m for m in g.metrics}
    assert by["files.corrupt"].value == 1 and by["files.corrupt"].status is Status.WARN
    assert by["files.duplicates"].value == 1 and by["files.duplicates"].status is Status.WARN
    disc.ignored = [ExcludedFile(path=Path(f"{i}.tif"), reason="0 바이트 파일") for i in range(3)]
    g = analyze_files(disc, [], 0, 0, cfg)
    by = {m.key: m for m in g.metrics}
    assert by["files.corrupt"].status is Status.FAIL  # 3 % ≥ 2 %
    assert by["files.duplicates"].status is Status.PASS


# --------------------------------------------------------------------------- 궤적


def test_trajectory_grid_numbers() -> None:
    cfg = load_config()
    caps = _grid(n_lines=2, per_line=6)
    ivals = intervals_s(caps)
    res = traj(caps, SensorKind.REDEDGE_MX, cfg.flight, cfg.rtk, 0, ivals)
    survey = [ln for ln in res.lines if not ln.is_transit]
    assert len(survey) == 2
    # UTM 격자 수렴각(zone 52, 경도 127°E) 때문에 진북 기준 90° 가 격자 기준 ≈ 91.2° 로 나온다
    assert survey[0].heading_deg == pytest.approx(90.0, abs=2.0)
    assert survey[1].heading_deg == pytest.approx(270.0, abs=2.0)
    assert survey[0].length_m == pytest.approx(100.0, rel=5e-3)
    assert res.line_spacing_m == pytest.approx(30.0, rel=2e-2)
    assert res.median_speed_mps == pytest.approx(5.0, rel=5e-3)
    # AGL: 첫 캡처 고도 기준 → 전부 0 (모두 같은 고도)
    # 지상 캡처가 없어 이륙점을 알 수 없음 → AGL N/A (FAIL 로 오판하지 않는다)
    assert res.takeoff_alt_msl_m is None and res.mean_agl_m is None
    assert res.agl_unknown_reason and "이륙점" in res.agl_unknown_reason
    by = {m.key: m for m in res.group.metrics}
    assert by["flight.agl_mean"].status is Status.NA
    assert (
        by["flight.agl_within_tolerance"].status is Status.NA
        and by["flight.agl_within_tolerance"].gate
    )
    assert by["flight.speed"].status is Status.PASS
    assert by["flight.off_nadir"].status is Status.PASS
    assert by["rtk.fixed_pct"].status is Status.NA
    assert res.track_length_m == pytest.approx(100 + 100 + 15 + 15, rel=5e-2)
    rows = res.rows
    assert rows[0].speed_mps is None and rows[1].speed_mps == pytest.approx(5.0, rel=5e-3)
    assert rows[1].heading_deg == pytest.approx(90.0, abs=2.0)
    assert rows[6].line != rows[5].line  # 전환 캡처는 다른 라인
    assert survey[1].n_captures == 6  # 꺾임 직전 캡처(라인 첫 점)도 새 라인에 편입
    assert res.bbox is not None and len(res.bbox) == 4


def test_trajectory_takeoff_override_and_tolerance() -> None:
    cfg = load_config()
    cfg.flight.takeoff_alt_msl_m = 100.0
    caps = _grid(n_lines=2, per_line=6)
    for k, c in enumerate(caps):
        c.alt_msl_m = 220.0 + (60.0 if k == 3 else 0.0)  # 캡처 3 만 180 m AGL
    res = traj(caps, SensorKind.REDEDGE_MX, cfg.flight, cfg.rtk, 0, intervals_s(caps))
    by = {m.key: m for m in res.group.metrics}
    assert "설정 이륙점" in res.agl_source
    assert by["flight.agl_mean"].status is Status.PASS
    assert by["flight.agl_within_tolerance"].value == pytest.approx(11 / 12 * 100, abs=0.1)
    assert by["flight.agl_within_tolerance"].status is Status.PASS  # 91.7 % ≥ 90
    assert "agl_out" in res.rows[3].flags
    assert by["flight.altitude_std"].status is Status.FAIL  # 라인 1 표준편차 ≈ 22.8 m


def test_trajectory_m3m_rtk_and_gimbal() -> None:
    cfg = load_config()
    caps = [
        _cap(i, i * 20.0, 0, sensor=SensorKind.DJI_M3M, rtk_flag=50 if i < 8 else 16, alt_rel=120.0)
        for i in range(10)
    ]
    for c in caps:
        c.gimbal_pitch_deg, c.gimbal_roll_deg = -90.0, 0.0
    caps[2].gimbal_pitch_deg = -78.0
    res = traj(caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 1, intervals_s(caps))
    by = {m.key: m for m in res.group.metrics}
    assert "RelativeAltitude" in res.agl_source and res.mean_agl_m == pytest.approx(120.0)
    assert by["rtk.fixed_pct"].value == 80.0 and by["rtk.fixed_pct"].status is Status.WARN  # 경계
    assert by["flight.off_nadir"].value == 10.0  # 1/10 = 10 % → PASS (> 10 % 아님)
    assert by["flight.off_nadir"].status is Status.PASS
    assert res.rows[2].off_nadir_deg == pytest.approx(12.0)
    assert res.rows[9].rtk_fixed is False and res.rows[0].rtk_fixed is True


def test_trajectory_no_coordinates() -> None:
    cfg = load_config()
    caps = [CaptureMeta(capture_id=str(i), sensor=SensorKind.REDEDGE_MX, index=i) for i in range(3)]
    res = traj(caps, SensorKind.REDEDGE_MX, cfg.flight, cfg.rtk, 0, [None, None, None])
    assert res.lines == [] and res.track_length_m is None and res.bbox is None
    by = {m.key: m for m in res.group.metrics}
    assert by["flight.agl_mean"].status is Status.NA and by["flight.speed"].status is Status.NA
    assert all(r.line is None for r in res.rows)


def test_trajectory_oblique_run_excluded_and_auto_targets() -> None:
    """DJI 고도 최적화: 임무 끝 짐벌 45° 경사 촬영은 판정에서 빠지고, 목표고도는 데이터 중앙값."""
    cfg = load_config()
    caps = [
        _cap(i, i * 20.0, 0, sensor=SensorKind.DJI_M3M, rtk_flag=50, alt_rel=300.0)
        for i in range(12)
    ]
    for c in caps:
        c.gimbal_pitch_deg, c.gimbal_roll_deg = -90.0, 0.0
    for c in caps[8:]:
        c.gimbal_pitch_deg = -45.0  # 끝 4장: 중심을 향한 경사 촬영
    res = traj(caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps))
    by = {m.key: m for m in res.group.metrics}
    assert res.oblique_indices == [8, 9, 10, 11]
    assert all("oblique" in res.rows[i].flags for i in range(8, 12))
    assert all("off_nadir" not in res.rows[i].flags for i in range(12))
    assert res.rows[9].line is None and res.rows[3].line is not None
    assert by["flight.off_nadir"].value == 0.0 and by["flight.off_nadir"].status is Status.PASS
    assert by["flight.oblique"].value == 4 and "제외" in by["flight.oblique"].reason
    assert "경사 촬영 4장 제외" in by["flight.lines"].reason
    # 목표고도·속도 미지정 → 중앙값 기준, 평균·속도는 정보 항목, 허용범위는 일관성 판정
    assert res.target_agl_m == pytest.approx(300.0) and "미지정" in res.target_agl_source
    assert by["flight.agl_mean"].status is Status.PASS and "미지정" in by["flight.agl_mean"].reason
    assert by["flight.agl_within_tolerance"].value == 100.0
    assert by["flight.speed"].status is Status.PASS
    assert "미지정" in (by["flight.speed"].threshold or "")
    assert all("agl_out" not in r.flags for r in res.rows)
    # 경사 촬영이 3장 미만이면 그냥 연직 이탈
    caps2 = [c.model_copy() for c in caps]
    for c in caps2[8:10]:
        c.gimbal_pitch_deg = -90.0
    res2 = traj(caps2, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps2))
    assert res2.oblique_indices == [] and "off_nadir" in res2.rows[11].flags


def test_trajectory_targets_from_mission_and_config() -> None:
    cfg = load_config()
    caps = [
        _cap(i, i * 20.0, 0, sensor=SensorKind.DJI_M3M, rtk_flag=50, alt_rel=300.0)
        for i in range(8)
    ]
    for c in caps:
        c.gimbal_pitch_deg, c.gimbal_roll_deg = -90.0, 0.0
    plan = MissionPlan(
        source="m.kmz",
        shoot_height_m=250.0,
        speed_mps=5.0,
        polygon_geojson={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    )
    res = traj(caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps), None, plan)
    by = {m.key: m for m in res.group.metrics}
    assert res.target_agl_m == 250.0 and res.target_agl_source == "임무 파일"
    assert by["flight.agl_mean"].status is Status.WARN  # 300 vs 250 = +20 %
    assert "임무 파일" in (by["flight.agl_mean"].threshold or "")
    assert res.target_speed_mps == 5.0 and by["flight.speed"].status is Status.PASS
    assert by["flight.agl_mean"].details["target_agl_m"] == 250.0
    # 설정값이 있으면 임무 파일보다 우선
    cfg.flight.target_agl_m = 120.0
    res2 = traj(caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps), None, plan)
    by2 = {m.key: m for m in res2.group.metrics}
    assert res2.target_agl_source == "설정" and by2["flight.agl_mean"].status is Status.FAIL
    assert by2["flight.agl_within_tolerance"].value == 0.0
