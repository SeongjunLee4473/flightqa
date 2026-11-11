"""Plotly 차트 (정적 JSON → 보고서 HTML 에 인라인, CLAUDE.md §5.1).

figure 는 순수 dict 로 만든다 (plotly.graph_objects 없이도 동작하도록). 색은 테마 토큰에서 오고,
배경은 투명이라 라이트/다크 전환 시 페이지 배경을 따른다. 글자색은 렌더 시 JS 가 채운다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from flightqa.core.models import (
    CaptureRow,
    Coverage,
    FlightReport,
    LineInfo,
    Status,
    SurveyArea,
)
from flightqa.theme.tokens import BAND_COLORS, LIGHT

Fig = dict[str, Any]
SERIES_H = 280
TICK_STANDOFF = 6
"""축선–눈금 숫자 여백 (px). x·y 동일."""
TRACK_COLOR = "#1E40AF"
"""궤적 선 (진한 파랑)."""
CAPTURE_OK_COLOR = "#3B82F6"
COVERAGE_LINE = "#EA580C"
"""커버리지 외곽선 (주황) — 위성 배경(초록·갈색)과 파란 궤적 위에서 모두 보인다."""
COVERAGE_FILL = "rgba(249,115,22,0.16)"
HOLE_LINE = "#DC2626"
HOLE_FILL = "rgba(220,38,38,0.35)"
SURVEY_LINE = "#7C3AED"
"""추정 조사 구역 외곽선 (보라, 점선) — 주황 커버리지·파란 궤적과 구분."""
SURVEY_FILL = "rgba(124,58,237,0.06)"
"""플래그 없는 캡처 마커 (궤적과 같은 계열의 밝은 파랑). 경고·실패는 상태색."""
"""시계열 차트 공통 높이 (px). 2 열 격자에서 행 높이가 맞도록 모두 같게 둔다."""
MAP_H = 480

FLAG_PRIORITY = [
    ("missing_band", "fail"),
    ("saturated", "fail"),
    ("rtk_not_fixed", "warn"),
    ("agl_out", "warn"),
    ("off_nadir", "warn"),
    ("irradiance_drop", "warn"),
    ("oblique", "na"),
    ("ground", "na"),
]


def _layout(title: str | None = None, **kw: Any) -> dict[str, Any]:
    """공통 레이아웃. Plotly 범례는 쓰지 않는다 — 템플릿 JS 가 트레이스 이름·색으로 HTML 범례를
    그래프 위 중앙에 그리고(대칭 줄바꿈), 클릭으로 트레이스를 켜고 끈다."""
    base: dict[str, Any] = {
        "margin": {"l": 56, "r": 60, "t": 20, "b": 44},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": "inherit", "size": 12},
        "hovermode": "closest",
        "xaxis": {"gridcolor": "rgba(128,128,128,0.18)", "zeroline": False},
        "yaxis": {"gridcolor": "rgba(128,128,128,0.18)", "zeroline": False},
    }
    if title:
        base["title"] = {"text": title, "font": {"size": 13}, "x": 0, "xanchor": "left"}
    base.update(kw)
    # 눈금 숫자와 축선 사이 여백: x·y(·y2) 모두 같은 값 (숫자가 축에 붙어 보이지 않게, 과하지 않게)
    for k, v in base.items():
        if k.startswith(("xaxis", "yaxis")) and isinstance(v, dict):
            v.setdefault("ticklabelstandoff", TICK_STANDOFF)
    return base


def _status_color(flags: str) -> str:
    fl = set(flags.split(",")) if flags else set()
    for f, kind in FLAG_PRIORITY:
        if f in fl:
            return {"fail": LIGHT.fail, "warn": LIGHT.warn, "na": LIGHT.na}[kind]
    return CAPTURE_OK_COLOR


def _rel(v: float | None, origin: float) -> float | None:
    return (v - origin) if v is not None else None


def _times(rows: Sequence[CaptureRow]) -> list[str | None]:
    return [r.time_utc.isoformat() if r.time_utc else None for r in rows]


def _elapsed_min(rows: Sequence[CaptureRow]) -> list[float | None]:
    t0 = next((r.time_utc for r in rows if r.time_utc), None)
    if t0 is None:
        return [None] * len(rows)
    return [
        round((r.time_utc - t0).total_seconds() / 60.0, 3) if r.time_utc else None for r in rows
    ]


# --------------------------------------------------------------------------- 차트


def _affine_fit(rows: Sequence[CaptureRow], x0: float, y0: float) -> list[float] | None:
    """(E−x0, N−y0) → (lon, lat) 최소제곱 아핀 근사 (지도 두 장의 확대 범위 동기화용).

    조사 구역 수 km 안에서는 UTM↔위경도가 사실상 선형이라 오차가 m 미만이다. 점이 3개 미만이거나
    한 줄에 늘어서 있으면 None.
    """
    pts = [
        (r.x_m - x0, r.y_m - y0, r.lon, r.lat)
        for r in rows
        if r.x_m is not None and r.y_m is not None and r.lon is not None and r.lat is not None
    ]
    if len(pts) < 3:
        return None
    import numpy as np

    a = np.array([[p[0], p[1], 1.0] for p in pts])
    if np.linalg.matrix_rank(a) < 3:
        return None
    lon = np.array([p[2] for p in pts])
    lat = np.array([p[3] for p in pts])
    cl, *_ = np.linalg.lstsq(a, lon, rcond=None)
    ct, *_ = np.linalg.lstsq(a, lat, rcond=None)
    return [float(v) for v in (*cl, *ct)]


def _geojson_rings(
    geojson: dict[str, Any] | None,
) -> tuple[list[list[list[float]]], list[list[list[float]]]]:
    """WGS84 (Multi)Polygon → (외곽 링 목록, 구멍 링 목록)."""
    if not geojson:
        return [], []
    polys: list[Any] = []
    if geojson.get("type") == "Polygon":
        polys = [geojson.get("coordinates", [])]
    elif geojson.get("type") == "MultiPolygon":
        polys = list(geojson.get("coordinates", []))
    outer: list[list[list[float]]] = []
    holes: list[list[list[float]]] = []
    for poly in polys:
        if not poly:
            continue
        outer.append([[float(p[0]), float(p[1])] for p in poly[0]])
        for ring in poly[1:]:
            holes.append([[float(p[0]), float(p[1])] for p in ring])
    return outer, holes


def trajectory_map(
    rows: Sequence[CaptureRow],
    lines: Sequence[LineInfo],
    coverage: Coverage | None,
    labels: dict[str, str],
    crs: str = "utm",
    survey: SurveyArea | None = None,
) -> Fig:
    """궤적·커버리지·추정 조사 구역 지도. ``crs``: ``utm`` (E/N, m, 상대좌표) 또는 ``wgs84``.

    두 지도는 ``layout.meta`` 의 아핀 계수로 확대 범위를 서로 맞춘다. 위경도 지도는 EPSG:4326 을
    그대로 그린다 (경도 1° 와 위도 1° 가 같은 길이 — QGIS 의 위경도 보기와 같고, 그래서 UTM 지도보다
    가로로 넓어 보인다).
    """
    xs = [r.x_m for r in rows]
    ys = [r.y_m for r in rows]
    x0 = min((x for x in xs if x is not None), default=0.0)
    y0 = min((y for y in ys if y is not None), default=0.0)
    ll = crs == "wgs84"
    if ll:
        px: list[float | None] = [r.lon for r in rows]
        py: list[float | None] = [r.lat for r in rows]
        cov_rings, cov_holes = _geojson_rings(coverage.geojson if coverage else None)
        sa_rings, _ = _geojson_rings(survey.geojson if survey else None)
    else:
        px = [_rel(x, x0) for x in xs]
        py = [_rel(y, y0) for y in ys]
        cov_rings = (
            [[[p[0] - x0, p[1] - y0] for p in ring] for ring in coverage.rings_m]
            if coverage
            else []
        )
        cov_holes = (
            [[[p[0] - x0, p[1] - y0] for p in ring] for ring in coverage.hole_rings_m]
            if coverage
            else []
        )
        sa_rings = (
            [[[p[0] - x0, p[1] - y0] for p in ring] for ring in survey.rings_m] if survey else []
        )
    data: list[dict[str, Any]] = []
    for k, ring in enumerate(cov_rings):
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [p[0] for p in ring],
                "y": [p[1] for p in ring],
                "fill": "toself",
                "fillcolor": COVERAGE_FILL,
                "line": {"color": COVERAGE_LINE, "width": 1.5},
                "name": labels.get("coverage", "coverage"),
                "legendrank": 2,
                "hoverinfo": "skip",
                "showlegend": k == 0,
            }
        )
    for k, ring in enumerate(cov_holes):
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [p[0] for p in ring],
                "y": [p[1] for p in ring],
                "fill": "toself",
                "fillcolor": HOLE_FILL,
                "line": {"color": HOLE_LINE, "width": 1.5, "dash": "dot"},
                "name": labels.get("hole", "hole"),
                "legendrank": 3,
                "hoverinfo": "skip",
                "showlegend": k == 0,
            }
        )
    data.append(
        {
            "type": "scatter",
            "mode": "lines",
            "x": px,
            "y": py,
            "line": {"color": TRACK_COLOR, "width": 2},
            "name": labels.get("track", "track"),
            "hoverinfo": "skip",
            "showlegend": False,
        }
    )
    line_of = {i: ln.index for ln in lines for i in range(ln.start_capture, ln.end_capture + 1)}
    text = [
        f"#{r.index} · {r.capture_id[:8]}<br>"
        + (f"{r.lat:.6f}, {r.lon:.6f}<br>" if r.lat is not None and r.lon is not None else "")
        + (f"AGL {r.agl_m:.1f} m · " if r.agl_m is not None else "")
        + (f"{r.speed_mps:.1f} m/s · " if r.speed_mps is not None else "")
        + f"line {line_of.get(r.index, '-')}<br>"
        + (r.flags or labels.get("none", "OK"))
        for r in rows
    ]
    data.append(
        {
            "type": "scattergl" if len(rows) > 2000 else "scatter",
            "mode": "markers",
            "x": px,
            "y": py,
            "marker": {
                "size": 5,
                "color": [_status_color(r.flags) for r in rows],
                "line": {"width": 0.8, "color": "rgba(255,255,255,0.9)"},
            },
            "text": text,
            "hoverinfo": "text",
            "name": labels.get("captures", "captures"),
            "legendrank": 4,
        }
    )
    # 조사 구역 외곽선은 맨 위에 그린다: 추정 구역(여유 0)은 바깥 조사 라인과 겹쳐서, 아래에 그리면
    # 궤적 선과 캡처 점에 가려 보이지 않았다. 채움 없이 선만, 범례 순서는 legendrank 로 맨 앞.
    from_mission = survey is not None and survey.method == "mission_kmz"
    for k, ring in enumerate(sa_rings):
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": [p[0] for p in ring],
                "y": [p[1] for p in ring],
                "line": {
                    "color": SURVEY_LINE,
                    "width": 2.5,
                    "dash": "solid" if from_mission else "dash",
                },
                "name": labels.get(
                    "mission_area" if from_mission else "survey_area", "survey area"
                ),
                "hoverinfo": "skip",
                "showlegend": k == 0,
                "legendrank": 1,
            }
        )
    grid = "rgba(128,128,128,0.18)"
    if ll:
        xaxis: dict[str, Any] = {
            "title": {"text": labels.get("lon", "Lon (°)")},
            "gridcolor": grid,
            "zeroline": False,
            "tickformat": ".4f",
            "hoverformat": ".6f",
            "nticks": 4,
            "tickangle": 0,
            "automargin": True,
        }
        yaxis: dict[str, Any] = {
            "title": {"text": labels.get("lat", "Lat (°)"), "standoff": 14},
            "scaleanchor": "x",
            "scaleratio": 1,
            "gridcolor": grid,
            "zeroline": False,
            "tickformat": ".4f",
            "hoverformat": ".6f",
            "nticks": 5,
            "automargin": True,
        }
    else:
        xaxis = {"title": {"text": "E (m)"}, "gridcolor": grid, "zeroline": False}
        yaxis = {
            "title": {"text": "N (m)"},
            "scaleanchor": "x",
            "scaleratio": 1,
            "gridcolor": grid,
            "zeroline": False,
        }
    meta: dict[str, Any] = {"kind": "map", "crs": crs}
    aff = _affine_fit(rows, x0, y0)
    if aff is not None:
        meta["affine"] = aff  # lon = a·x + b·y + c ; lat = d·x + e·y + f  (x, y: 상대 m)
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis=xaxis,
            yaxis=yaxis,
            height=MAP_H,
            margin={"l": 96 if ll else 56, "r": 44, "t": 20, "b": 48},
            modebar={"orientation": "v"},
            meta=meta,
            dragmode="pan",
        ),
    }


def altitude_speed(
    rows: Sequence[CaptureRow],
    target_agl: float,
    target_speed: float,
    labels: dict[str, str],
    terrain_follow: bool = False,
) -> Fig:
    t = _elapsed_min(rows)
    data = [
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.agl_m for r in rows],
            "name": labels.get("agl_rel", "alt. (m)") if terrain_follow else "AGL (m)",
            "line": {"color": LIGHT.brand, "width": 1.5},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [target_agl, target_agl],
            "name": f"{labels.get('target', 'target')} AGL",
            "line": {"color": LIGHT.brand, "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.speed_mps for r in rows],
            "name": "speed (m/s)",
            "yaxis": "y2",
            "line": {"color": BAND_COLORS["nir"], "width": 1},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [target_speed, target_speed],
            "name": f"{labels.get('target', 'target')} speed",
            "yaxis": "y2",
            "line": {"color": BAND_COLORS["nir"], "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
    ]
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis={"title": {"text": "min"}, "gridcolor": "rgba(128,128,128,0.18)"},
            yaxis={
                "title": {"text": "AGL (m)"},
                "gridcolor": "rgba(128,128,128,0.18)",
                "rangemode": "tozero",
            },
            yaxis2={
                "title": {"text": "m/s"},
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "rangemode": "tozero",
            },
            height=SERIES_H,
        ),
    }


def irradiance_solar(rows: Sequence[CaptureRow], labels: dict[str, str], solar_min: float) -> Fig:
    t = _elapsed_min(rows)
    ev_x = [t[i] for i, r in enumerate(rows) if "irradiance_drop" in r.flags]
    ev_y = [r.irradiance for r in rows if "irradiance_drop" in r.flags]
    data: list[dict[str, Any]] = [
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.irradiance for r in rows],
            "name": "irradiance",
            "line": {"color": BAND_COLORS["green"], "width": 1.5},
        },
        {
            "type": "scatter",
            "mode": "markers",
            "x": ev_x,
            "y": ev_y,
            "name": labels.get("event", "event"),
            "marker": {"color": LIGHT.fail, "size": 8, "symbol": "x"},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.solar_elevation_calc_deg for r in rows],
            "name": "solar elev. (°)",
            "yaxis": "y2",
            "line": {"color": BAND_COLORS["red"], "width": 1},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [solar_min, solar_min],
            "name": f"{labels.get('target', 'target')} solar",
            "yaxis": "y2",
            "line": {"color": BAND_COLORS["red"], "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
    ]
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis={"title": {"text": "min"}, "gridcolor": "rgba(128,128,128,0.18)"},
            yaxis={
                "title": {"text": "irradiance"},
                "gridcolor": "rgba(128,128,128,0.18)",
                "rangemode": "tozero",
            },
            yaxis2={
                "title": {"text": "°"},
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "range": [0, 90],
            },
            height=SERIES_H,
        ),
    }


def saturation_overlap(
    rows: Sequence[CaptureRow], sat_warn: float, fwd_warn: float, labels: dict[str, str]
) -> Fig:
    t = _elapsed_min(rows)
    data: list[dict[str, Any]] = [
        {
            "type": "bar",
            "x": t,
            "y": [r.sat_max_pct for r in rows],
            "name": "max saturation (%)",
            "marker": {"color": LIGHT.warn},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [sat_warn, sat_warn],
            "name": f"{labels.get('target', 'target')} sat.",
            "line": {"color": LIGHT.warn, "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.forward_overlap_pct for r in rows],
            "name": "forward overlap (%)",
            "yaxis": "y2",
            "line": {"color": LIGHT.brand, "width": 1.2},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [fwd_warn, fwd_warn],
            "name": f"{labels.get('target', 'target')} overlap",
            "yaxis": "y2",
            "line": {"color": LIGHT.brand, "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
    ]
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis={"title": {"text": "min"}, "gridcolor": "rgba(128,128,128,0.18)"},
            yaxis={
                "title": {"text": "%"},
                "gridcolor": "rgba(128,128,128,0.18)",
                "rangemode": "tozero",
            },
            yaxis2={
                "title": {"text": "overlap %"},
                "overlaying": "y",
                "side": "right",
                "showgrid": False,
                "range": [0, 100],
            },
            height=SERIES_H,
            bargap=0.05,
        ),
    }


def attitude(rows: Sequence[CaptureRow], nadir_warn: float, labels: dict[str, str]) -> Fig:
    t = _elapsed_min(rows)
    data = [
        {
            "type": "scatter",
            "mode": "lines",
            "x": t,
            "y": [r.off_nadir_deg for r in rows],
            "name": "off-nadir (°)",
            "line": {"color": BAND_COLORS["rededge"], "width": 1.2},
        },
        {
            "type": "scatter",
            "mode": "lines",
            "x": [t[0], t[-1]] if t else [],
            "y": [nadir_warn, nadir_warn],
            "name": labels.get("target", "target"),
            "line": {"color": BAND_COLORS["rededge"], "width": 1, "dash": "dash"},
            "hoverinfo": "skip",
        },
    ]
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis={"title": {"text": "min"}, "gridcolor": "rgba(128,128,128,0.18)"},
            yaxis={
                "title": {"text": "°"},
                "gridcolor": "rgba(128,128,128,0.18)",
                "rangemode": "tozero",
            },
            height=SERIES_H,
        ),
    }


def interval_hist(rows: Sequence[CaptureRow]) -> Fig:
    vals = [r.interval_s for r in rows if r.interval_s is not None and r.interval_s > 0]
    return {
        "data": [
            {
                "type": "histogram",
                "x": vals,
                "nbinsx": 40,
                "marker": {"color": LIGHT.brand},
                "name": "interval (s)",
            }
        ],
        "layout": _layout(
            None,
            xaxis={"title": {"text": "s"}, "gridcolor": "rgba(128,128,128,0.18)"},
            yaxis={"title": {"text": "captures"}, "gridcolor": "rgba(128,128,128,0.18)"},
            height=SERIES_H,
            showlegend=False,
        ),
    }


def band_histograms(band_hist: dict[str, list[int]], edges: list[int], sat_dn: int) -> Fig:
    data: list[dict[str, Any]] = []
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)] if edges else []
    for band, counts in band_hist.items():
        total = sum(counts) or 1
        data.append(
            {
                "type": "scatter",
                "mode": "lines",
                "x": centers,
                "y": [c / total * 100.0 for c in counts],
                "name": band,
                "line": {"color": BAND_COLORS.get(band, LIGHT.na), "width": 1.5, "shape": "hv"},
            }
        )
    shapes = [
        {
            "type": "line",
            "x0": sat_dn,
            "x1": sat_dn,
            "y0": 0,
            "y1": 1,
            "yref": "paper",
            "line": {"color": LIGHT.fail, "width": 1, "dash": "dot"},
        }
    ]
    return {
        "data": data,
        "layout": _layout(
            None,
            xaxis={
                "title": {"text": "DN"},
                "gridcolor": "rgba(128,128,128,0.18)",
                "range": [0, 65536],
            },
            yaxis={
                "title": {"text": "% pixels"},
                "type": "log",
                "gridcolor": "rgba(128,128,128,0.18)",
            },
            height=SERIES_H,
            shapes=shapes,
        ),
    }


def _target(f: FlightReport, key: str, name: str) -> float:
    """판정 항목이 기록한 목표값 (설정·임무 파일·중앙값). 없으면 NaN → 기준선을 그리지 않는다."""
    for g in f.groups:
        for m in g.metrics:
            if m.key == key:
                v = (m.details or {}).get(name)
                return float(v) if isinstance(v, int | float) else float("nan")
    return float("nan")


def flight_charts(
    f: FlightReport, cfg: dict[str, Any], labels: dict[str, str], sat_dn: int
) -> dict[str, Fig]:
    """비행 하나의 모든 차트. 키는 템플릿의 div id 접미사."""
    flight_cfg = cfg.get("flight", {})
    exp_cfg = cfg.get("exposure", {})
    ov_cfg = cfg.get("overlap", {})
    irr_cfg = cfg.get("irradiance", {})
    charts: dict[str, Fig] = {
        "map": trajectory_map(f.rows, f.lines, f.coverage, labels, crs="utm", survey=f.survey_area),
        "mapll": trajectory_map(
            f.rows, f.lines, f.coverage, labels, crs="wgs84", survey=f.survey_area
        ),
        "alt": altitude_speed(
            f.rows,
            _target(f, "flight.agl_mean", "target_agl_m"),
            _target(f, "flight.speed", "target_speed_mps"),
            labels,
            f.terrain_follow,
        ),
        "irr": irradiance_solar(f.rows, labels, float(irr_cfg.get("solar_elevation_min_deg", 30))),
        "sat": saturation_overlap(
            f.rows,
            float(exp_cfg.get("saturated_pixel_warn_pct", 0.5)),
            float(ov_cfg.get("forward_warn_pct", 75)),
            labels,
        ),
        "att": attitude(f.rows, float(flight_cfg.get("nadir_warn_deg", 5)), labels),
        "int": interval_hist(f.rows),
    }
    if f.band_hist:
        charts["hist"] = band_histograms(f.band_hist, f.hist_edges, sat_dn)
    return charts


def status_counts(f: FlightReport) -> dict[str, int]:
    out = {s.value: 0 for s in Status}
    for g in f.groups:
        for m in g.metrics:
            out[m.status.value] += 1
    return out


def finite(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else v
