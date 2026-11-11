"""임무 경로 지도 (GUI 임무 경로 화면의 미리보기).

보고서 지도와 같은 재료로 만든다: Plotly 정적 JSON, UTM(m)·위경도 두 좌표계, 아핀 계수로 확대
범위 연동, 휠 확대, 위성 배경(공용 ``_basemap.js.j2``). 임무마다 폴리곤 + 웨이포인트 순서(시작
초록 · 끝 빨강)를 그리고 꼭짓점·웨이포인트에 마우스를 올리면 좌표가 나온다.
``window.__fq_select(i)`` 로 임무 하나에 맞춰 확대하고, -1 이면 전체가 보이게 돌아간다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from jinja2 import Environment, PackageLoader, select_autoescape
from plotly.offline import get_plotlyjs
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, shape

from flightqa.core.models import MissionPlan
from flightqa.report.charts import MAP_H, SURVEY_LINE, _layout
from flightqa.report.html import BASEMAPS
from flightqa.theme.tokens import FONT_UI, LIGHT, css_root

POLY_FILL = "rgba(124,58,237,0.12)"
POLY_FILL_SEL = "rgba(124,58,237,0.30)"
WP_LINE = "#64748B"
START = LIGHT.pass_
END = LIGHT.fail

Fig = dict[str, Any]


def utm_epsg_of(plans: Sequence[MissionPlan]) -> int | None:
    xs: list[float] = []
    ys: list[float] = []
    for p in plans:
        g = shape(p.polygon_geojson)
        if not g.is_empty:
            xs.append(g.centroid.x)
            ys.append(g.centroid.y)
    if not xs:
        return None
    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _polys(g: Any) -> list[Polygon]:
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return []


def _affine(pairs: list[tuple[float, float, float, float]]) -> list[float] | None:
    """(x, y, lon, lat) 최소제곱 아핀: lon = a·x + b·y + c, lat = d·x + e·y + f."""
    if len(pairs) < 3:
        return None
    a = np.array([[p[0], p[1], 1.0] for p in pairs])
    if np.linalg.matrix_rank(a) < 3:
        return None
    lon = np.array([p[2] for p in pairs])
    lat = np.array([p[3] for p in pairs])
    cl, *_ = np.linalg.lstsq(a, lon, rcond=None)
    ct, *_ = np.linalg.lstsq(a, lat, rcond=None)
    return [float(v) for v in (*cl, *ct)]


def build_figures(plans: Sequence[MissionPlan], selected: int = -1) -> dict[str, Any]:
    """{"utm": fig, "wgs84": fig, "epsg": int|None}. ``layout.meta`` 에 crs·affine·bounds."""
    epsg = utm_epsg_of(plans)
    tr = Transformer.from_crs(4326, epsg, always_xy=True) if epsg else None

    def xy(lon: float, lat: float) -> tuple[float, float]:
        if tr is None:
            return lon, lat
        x, y = tr.transform(lon, lat)
        return float(x), float(y)

    figs: dict[str, Any] = {"epsg": epsg}
    pairs: list[tuple[float, float, float, float]] = []
    for crs in ("utm", "wgs84"):
        data: list[dict[str, Any]] = []
        bounds: list[list[float] | None] = []
        for i, p in enumerate(plans):
            sel = i == selected
            name = Path(p.source).stem
            bx: list[float] = []
            by: list[float] = []
            for poly in _polys(shape(p.polygon_geojson)):
                xs: list[float] = []
                ys: list[float] = []
                txt: list[str] = []
                for k, (lon, lat) in enumerate(poly.exterior.coords):
                    x, y = xy(lon, lat)
                    if crs == "utm":
                        xs.append(x)
                        ys.append(y)
                        txt.append(f"{name}<br>꼭짓점 {k + 1}<br>E {x:,.1f} · N {y:,.1f} m")
                        if tr is not None:
                            pairs.append((x, y, lon, lat))
                    else:
                        xs.append(lon)
                        ys.append(lat)
                        txt.append(f"{name}<br>꼭짓점 {k + 1}<br>{lat:.6f}, {lon:.6f}")
                bx += xs
                by += ys
                data.append(
                    {
                        "type": "scatter",
                        "mode": "lines",
                        "x": xs,
                        "y": ys,
                        "fill": "toself",
                        "fillcolor": POLY_FILL_SEL if sel else POLY_FILL,
                        "line": {"color": SURVEY_LINE, "width": 2.6 if sel else 1.6},
                        "name": name,
                        "text": txt,
                        "hoverinfo": "text",
                        "meta": {"plan": i},
                    }
                )
            if p.waypoints_geojson:
                wx: list[float] = []
                wy: list[float] = []
                wt: list[str] = []
                for k, (lon, lat) in enumerate(shape(p.waypoints_geojson).coords):
                    x, y = xy(lon, lat)
                    if crs == "utm":
                        wx.append(x)
                        wy.append(y)
                        wt.append(f"{name}<br>웨이포인트 {k}<br>E {x:,.1f} · N {y:,.1f} m")
                    else:
                        wx.append(lon)
                        wy.append(lat)
                        wt.append(f"{name}<br>웨이포인트 {k}<br>{lat:.6f}, {lon:.6f}")
                bx += wx
                by += wy
                data.append(
                    {
                        "type": "scatter",
                        "mode": "lines+markers",
                        "x": wx,
                        "y": wy,
                        "line": {"color": WP_LINE, "width": 1.4 if sel else 1.0, "dash": "dot"},
                        "marker": {"size": 5 if sel else 4, "color": WP_LINE},
                        "name": f"{name} 웨이포인트",
                        "text": wt,
                        "hoverinfo": "text",
                        "meta": {"plan": i},
                    }
                )
                for idx, color, lab in ((0, START, "시작"), (-1, END, "끝")):
                    data.append(
                        {
                            "type": "scatter",
                            "mode": "markers",
                            "x": [wx[idx]],
                            "y": [wy[idx]],
                            "marker": {
                                "size": 11,
                                "color": color,
                                "line": {"width": 1.5, "color": "#fff"},
                            },
                            "name": f"{name} {lab}",
                            "text": [f"{name}<br>{lab}<br>" + wt[idx].split("<br>")[-1]],
                            "hoverinfo": "text",
                            "meta": {"plan": i},
                        }
                    )
            bounds.append([min(bx), min(by), max(bx), max(by)] if bx else None)
        # 축·여백·눈금 여백은 보고서 지도(charts.trajectory_map)와 같은 규약
        # (_layout 이 ticklabelstandoff 를 채운다)
        grid = "rgba(128,128,128,0.18)"
        if crs == "utm":
            xaxis: dict[str, Any] = {
                "title": {"text": f"E (m) · EPSG:{epsg}" if epsg else "x"},
                "gridcolor": grid,
                "zeroline": False,
                "tickformat": ",.0f",
                "hoverformat": ",.1f",
                "exponentformat": "none",
                "automargin": True,
            }
            yaxis: dict[str, Any] = {
                "title": {"text": "N (m)", "standoff": 14},
                "scaleanchor": "x",
                "scaleratio": 1,
                "gridcolor": grid,
                "zeroline": False,
                "tickformat": ",.0f",
                "hoverformat": ",.1f",
                "exponentformat": "none",
                "automargin": True,
            }
        else:
            # EPSG:4326 그대로: 경도 1° 와 위도 1° 를 같은 길이로 (QGIS 의 위경도 보기와 같다)
            xaxis = {
                "title": {"text": "경도 (°) · EPSG:4326"},
                "gridcolor": grid,
                "zeroline": False,
                "tickformat": ".4f",
                "hoverformat": ".6f",
                "nticks": 6,
                "tickangle": 0,
                "automargin": True,
            }
            yaxis = {
                "title": {"text": "위도 (°)", "standoff": 14},
                "scaleanchor": "x",
                "scaleratio": 1,
                "gridcolor": grid,
                "zeroline": False,
                "tickformat": ".4f",
                "hoverformat": ".6f",
                "nticks": 6,
                "automargin": True,
            }
        figs[crs] = {
            "data": data,
            "layout": _layout(
                None,
                xaxis=xaxis,
                yaxis=yaxis,
                height=MAP_H,
                margin={"l": 96 if crs == "wgs84" else 56, "r": 44, "t": 20, "b": 48},
                modebar={"orientation": "v"},
                meta={"kind": "map", "crs": crs, "bounds": bounds},
                dragmode="pan",
                showlegend=False,
            ),
        }
    aff = _affine(pairs)
    if aff is not None:
        figs["utm"]["layout"]["meta"]["affine"] = aff
        figs["wgs84"]["layout"]["meta"]["affine"] = aff
    return figs


def _env() -> Environment:
    return Environment(
        loader=PackageLoader("flightqa.report", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )


def basemap_options(report_cfg: dict[str, Any] | None) -> dict[str, Any]:
    rep = report_cfg or {}
    provider = str(rep.get("basemap_provider", "esri"))
    url, attribution = BASEMAPS.get(provider, ("", ""))
    if provider == "custom":
        url = str(rep.get("basemap_url", ""))
        attribution = str(rep.get("basemap_attribution", ""))
    key = str(rep.get("basemap_api_key", ""))
    if "{key}" in url and not key:
        url = ""
    return {
        "url": url,
        "key": key,
        "attribution": attribution,
        "maxZoom": int(rep.get("basemap_max_zoom", 19)),
    }


def render_mission_map(
    plans: Sequence[MissionPlan],
    selected: int = -1,
    *,
    report_cfg: dict[str, Any] | None = None,
    theme: str = "light",
) -> str:
    figs = build_figures(plans, selected)
    tpl = _env().get_template("mission_map.html.j2")
    return tpl.render(
        theme=theme,
        css_root=css_root(),
        plotly_js=get_plotlyjs(),
        figs_json=json.dumps(
            {"utm": figs["utm"], "wgs84": figs["wgs84"]}, ensure_ascii=False, separators=(",", ":")
        ),
        options_json=json.dumps(
            {"font": FONT_UI, "basemap": basemap_options(report_cfg), "epsg": figs["epsg"]},
            ensure_ascii=False,
        ),
        n_plans=len(plans),
        selected=selected,
    )


def write_mission_map(
    plans: Sequence[MissionPlan],
    path: Path,
    selected: int = -1,
    *,
    report_cfg: dict[str, Any] | None = None,
    theme: str = "light",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_mission_map(plans, selected, report_cfg=report_cfg, theme=theme), encoding="utf-8"
    )
    return path
