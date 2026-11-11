"""지형추종 감지·DEM 기준 AGL·PPK 힌트 (합성 데이터).

실제 현장 자료는 쓰지 않는다. 좌표는 임의(37°N, 127°E 근처의 가짜 격자).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import tifffile
from pyproj import Transformer

from flightqa.config.schema import load_config
from flightqa.core.analysis.dem import DemError, DemSampler
from flightqa.core.analysis.integrity import intervals_s
from flightqa.core.analysis.trajectory import TrajectoryResult, analyze_trajectory
from flightqa.core.models import (
    BandMeta,
    CaptureMeta,
    CheckGroup,
    Metric,
    RtkInfo,
    SensorKind,
    Status,
)
from flightqa.core.pipeline import _add_ppk_hint, _ppk_files

T0 = datetime(2026, 6, 1, 1, 0, tzinfo=UTC)
EPSG = 32652
_TO_LL = Transformer.from_crs(EPSG, 4326, always_xy=True)
X0, Y0 = 300_000.0, 4_100_000.0  # UTM 52N 안의 임의 원점 (실제 현장 아님)


def _cap(
    i: int, x: float, y: float, *, alt_rel: float | None, alt_msl: float | None
) -> CaptureMeta:
    lon, lat = _TO_LL.transform(X0 + x, Y0 + y)
    t = T0 + timedelta(seconds=2.0 * i)
    m = CaptureMeta(
        capture_id=f"c{i}",
        sensor=SensorKind.DJI_M3M,
        index=i,
        time_utc=t,
        lat=lat,
        lon=lon,
        alt_msl_m=alt_msl,
        alt_rel_m=alt_rel,
        gimbal_pitch_deg=-90.0,
        gimbal_roll_deg=0.0,
        rtk=RtkInfo(flag=50, fixed=True),
    )
    for b in ("green", "red", "rededge", "nir"):
        m.bands[b] = BandMeta(band=b, path=Path(f"{i}_{b}.tif"), time_utc=t, mtime_utc=t)
    return m


def terrain(x: float, y: float) -> float:
    """가짜 지형: 동쪽으로 오르막 + 남북 방향 굴곡 (m)."""
    return 150.0 + 0.08 * x + 8.0 * math.sin(y / 40.0)


AltFn = Callable[[float, float], tuple[float | None, float | None]]


def _grid(
    alt_fn: AltFn, n_lines: int = 4, per_line: int = 14, step: float = 7.0, spacing: float = 10.0
) -> list[CaptureMeta]:
    """남북 왕복 라인 (M3M 스타일: 2 s 간격, 3.5 m/s). alt_fn(x, y) → (alt_rel, alt_msl)."""
    caps: list[CaptureMeta] = []
    i = 0
    for ln in range(n_lines):
        ys = [k * step for k in range(per_line)]
        if ln % 2:
            ys = ys[::-1]
        x = ln * spacing
        for y in ys:
            rel, msl = alt_fn(x, y)
            caps.append(_cap(i, x, y, alt_rel=rel, alt_msl=msl))
            i += 1
    return caps


def _run(
    caps: list[CaptureMeta], dem: DemSampler | None = None
) -> tuple[TrajectoryResult, dict[str, Metric]]:
    cfg = load_config()
    cfg.flight.target_agl_m = 60.0
    cfg.flight.target_speed_mps = 3.5
    res = analyze_trajectory(
        caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps), dem
    )
    return res, {m.key: m for m in res.group.metrics}


# --------------------------------------------------------------------------- 지형추종


def test_terrain_follow_detected() -> None:
    takeoff = terrain(0, 0)
    # DSM 추종: 실제 AGL 60 m 유지 → 이륙점 기준 상대고도는 지형을 따라 오르내린다
    caps = _grid(lambda x, y: (terrain(x, y) + 60.0 - takeoff, terrain(x, y) + 60.0))
    res, by = _run(caps)
    assert res.terrain_follow is True
    ev = res.terrain_follow_evidence
    assert ev["range_m"] >= 5.0 and ev["corr"] >= 0.8 and ev["n_pairs"] >= 2
    assert by["flight.terrain_follow"].value == "예"
    assert by["flight.agl_mean"].status is Status.NA
    assert (
        by["flight.agl_within_tolerance"].status is Status.NA
        and by["flight.agl_within_tolerance"].value is None
    )
    assert by["flight.altitude_std"].status is Status.NA
    assert "지형추종" in res.agl_source
    assert not any("agl_out" in r.flags for r in res.rows)
    assert (
        by["flight.terrain_follow"].next_action
        and "dem_path" in by["flight.terrain_follow"].next_action
    )


def test_terrain_follow_not_detected_constant_altitude() -> None:
    rng = random.Random(1)
    caps = _grid(lambda x, y: (60.0 + rng.uniform(-0.3, 0.3), 210.0))
    res, by = _run(caps)
    assert res.terrain_follow is False
    assert res.terrain_follow_evidence["range_m"] < 5.0
    assert by["flight.terrain_follow"].value == "아니오"
    assert by["flight.agl_mean"].status is Status.PASS
    assert by["flight.agl_within_tolerance"].status is Status.PASS


def test_terrain_follow_not_detected_random_altitude() -> None:
    """변화폭은 크지만 라인마다 무작위(바람·고도 유지 실패)면 상관이 낮아 지형추종이 아니다."""
    rng = random.Random(7)
    caps = _grid(lambda x, y: (60.0 + rng.uniform(-12.0, 12.0), 210.0))
    res, by = _run(caps)
    assert res.terrain_follow_evidence["range_m"] >= 5.0
    assert res.terrain_follow is False
    assert by["flight.altitude_std"].status in (
        Status.WARN,
        Status.FAIL,
    )  # 실제 고도 불안정은 그대로 판정


def test_terrain_follow_disabled_by_config() -> None:
    takeoff = terrain(0, 0)
    caps = _grid(lambda x, y: (terrain(x, y) + 60.0 - takeoff, terrain(x, y) + 60.0))
    cfg = load_config()
    cfg.flight.terrain_follow_detect = False
    res = analyze_trajectory(caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps))
    assert res.terrain_follow is False and res.terrain_follow_evidence == {}


# --------------------------------------------------------------------------- DEM


def _write_dem(
    path: Path, *, px: float = 5.0, rows: int = 60, cols: int = 60, nodata: bool = True
) -> None:
    jj, ii = np.mgrid[0:rows, 0:cols]
    xc = (ii + 0.5) * px - 50.0  # 원점 X0−50 부터
    yc = (rows - jj - 0.5) * px - 50.0
    z = np.vectorize(terrain)(xc, yc).astype(np.float32)
    if nodata:
        z[0, 0] = -9999.0
    geokeys = [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, EPSG]
    extratags: list[tuple[int, str, int, Any, bool]] = [
        (33550, "d", 3, (px, px, 0.0), True),
        (33922, "d", 6, (0, 0, 0, X0 - 50.0, Y0 - 50.0 + rows * px, 0), True),
        (34735, "H", len(geokeys), tuple(geokeys), True),
    ]
    if nodata:
        extratags.append((42113, "s", 0, "-9999", True))
    tifffile.imwrite(path, z, extratags=extratags)


def test_dem_sampler_bilinear_and_nodata(tmp_path: Path) -> None:
    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path)
    d = DemSampler(dem_path)
    assert d.epsg == EPSG and d.cols == 60 and "EPSG:32652" in d.describe()
    pts = [(12.3, 45.6), (100.0, 100.0), (240.0, 3.0)]
    lons, lats = zip(*[_TO_LL.transform(X0 + x, Y0 + y) for x, y in pts], strict=True)
    got = d.sample(list(lons), list(lats))
    for (x, y), g in zip(pts, got, strict=True):
        assert g == pytest.approx(terrain(x, y), abs=0.4)  # 쌍선형 보간 vs 사인 지형
    # 격자 밖 → NaN, nodata 픽셀(좌상단) → NaN, None 좌표 → NaN
    out = d.sample(
        [_TO_LL.transform(X0 + 900, Y0)[0], None], [_TO_LL.transform(X0 + 900, Y0)[1], None]
    )
    assert all(math.isnan(v) for v in out)
    lon, lat = _TO_LL.transform(X0 - 50.0 + 2.5, Y0 - 50.0 + 60 * 5.0 - 2.5)
    assert math.isnan(d.sample([lon], [lat])[0])


def test_dem_errors(tmp_path: Path) -> None:
    with pytest.raises(DemError):
        DemSampler(tmp_path / "missing.tif")
    plain = tmp_path / "plain.tif"
    tifffile.imwrite(plain, np.zeros((4, 4), dtype=np.float32))
    with pytest.raises(DemError):
        DemSampler(plain)


def test_true_agl_from_dem(tmp_path: Path) -> None:
    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path, nodata=False)
    takeoff = terrain(0, 0)
    geoid = 25.0
    # 기체 타원체고 = 정표고 지형 + 60 m AGL + 지오이드고
    caps = _grid(lambda x, y: (terrain(x, y) + 60.0 - takeoff, terrain(x, y) + 60.0 + geoid))
    cfg = load_config()
    cfg.flight.target_agl_m = 60.0
    cfg.flight.geoid_offset_m = geoid
    dem = DemSampler(dem_path)
    res = analyze_trajectory(
        caps, SensorKind.DJI_M3M, cfg.flight, cfg.rtk, 0, intervals_s(caps), dem
    )
    by = {m.key: m for m in res.group.metrics}
    assert res.agl_is_true and "DEM" in res.agl_source and res.terrain_follow is False
    assert res.mean_agl_m == pytest.approx(60.0, abs=0.5)
    assert by["flight.agl_mean"].status is Status.PASS
    assert by["flight.agl_within_tolerance"].value == pytest.approx(100.0)
    assert by["flight.altitude_std"].status is Status.PASS
    assert "DEM" in by["flight.terrain_follow"].reason


def test_dem_not_covering_falls_back(tmp_path: Path) -> None:
    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path, rows=4, cols=4)  # 20 m × 20 m 만 덮음
    caps = _grid(lambda x, y: (60.0, 210.0))
    res, _by = _run(caps, DemSampler(dem_path))
    assert not res.agl_is_true and "RelativeAltitude" in res.agl_source
    assert res.agl_unknown_reason and "덮지 않아" in res.agl_unknown_reason
    assert res.mean_agl_m == pytest.approx(60.0)


# --------------------------------------------------------------------------- PPK 힌트


def test_ppk_files_and_hint(tmp_path: Path) -> None:
    assert _ppk_files(tmp_path) == []
    for name in ("M_Timestamp.MRK", "M_PPKOBS.obs", "M_PPKRAW.bin", "note.txt"):
        (tmp_path / name).write_bytes(b"x")
    files = _ppk_files(tmp_path)
    assert files == ["M_PPKOBS.obs", "M_PPKRAW.bin", "M_Timestamp.MRK"]
    g = CheckGroup(
        key="flight",
        label="f",
        metrics=[
            Metric(
                key="rtk.fixed_pct", label="r", status=Status.FAIL, reason="", next_action="재비행"
            ),
            Metric(key="flight.speed", label="s", status=Status.WARN, reason="", next_action="x"),
        ],
    )
    _add_ppk_hint(g, files)
    assert g.metrics[0].next_action and "PPK" in g.metrics[0].next_action
    assert (
        "Timestamp.MRK" in g.metrics[0].next_action and g.metrics[0].details["ppk_files"] == files
    )
    assert g.metrics[1].next_action == "x"
    g.metrics[0].status = Status.PASS
    g.metrics[0].next_action = None
    _add_ppk_hint(g, files)
    assert g.metrics[0].next_action is None  # 통과면 힌트 없음
