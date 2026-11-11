"""§4.4 실측 중복도·커버리지.

- 캡처별 지상 커버리지 사각형 = 카메라 물리값 × AGL, 진행방향으로 회전. 이미지 가로(width) 축이
  진행방향에 수직(across-track)이라고 본다 (RedEdge·M3M 모두 통상 장착 방향).
- 종중복: 같은 조사 라인의 연속 캡처 거리 d 와 along-track 길이 h → (h − d)/h.
- 횡중복: 라인 간격 s 와 across-track 폭 w → (w − s)/w.
- 커버리지 합집합(shapely)으로 면적과 내부 공백(hole)을 구하고 WGS84 GeoJSON 으로 내보낸다.
비행계획의 설정값이 아니라 **실제 궤적·고도** 에서 나온 값이다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from typing import Any

from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from flightqa.config.schema import OverlapConfig
from flightqa.core.models import (
    CameraModel,
    CaptureRow,
    CheckGroup,
    Coverage,
    LineInfo,
    Metric,
    Status,
)
from flightqa.core.rules import fmt, grade_min, info_metric


def footprint_polygon(
    x: float, y: float, heading_deg: float, width_m: float, height_m: float
) -> Polygon:
    """중심 (x, y), 진행방향 heading(북=0, 시계방향). 가로 폭은 진행방향에 수직."""
    th = math.radians(heading_deg)
    # 진행방향 단위벡터 (dx, dy), 수직(우측) 벡터 (dy, -dx)
    fx, fy = math.sin(th), math.cos(th)
    rx, ry = fy, -fx
    hw, hh = width_m / 2.0, height_m / 2.0
    pts = [
        (x + rx * hw + fx * hh, y + ry * hw + fy * hh),
        (x - rx * hw + fx * hh, y - ry * hw + fy * hh),
        (x - rx * hw - fx * hh, y - ry * hw - fy * hh),
        (x + rx * hw - fx * hh, y + ry * hw - fy * hh),
    ]
    return Polygon(pts)


def footprints(
    rows: Sequence[CaptureRow],
    lines: Sequence[LineInfo],
    camera: CameraModel,
    to_xy: Callable[[float, float], tuple[float, float]] | None = None,
) -> list[tuple[int, Polygon]]:
    """조사 라인(전환 구간 제외) 캡처의 지상 커버리지 사각형 목록 [(캡처 index, Polygon)].

    좌표는 기본으로 행의 UTM ``x_m, y_m`` 이고, ``to_xy(lon, lat)`` 를 주면 그 투영을 쓴다
    (여러 비행을 한 래스터에 합칠 때). 사각형 방향은 라인 방향, 없으면 캡처 진행방향.
    """
    survey = [ln for ln in lines if not ln.is_transit]
    line_of = {i: ln.index for ln in survey for i in range(ln.start_capture, ln.end_capture + 1)}
    out: list[tuple[int, Polygon]] = []
    for i, r in enumerate(rows):
        if r.agl_m is None or r.agl_m <= 0 or i not in line_of:
            continue
        if to_xy is not None:
            if r.lon is None or r.lat is None:
                continue
            x, y = to_xy(r.lon, r.lat)
        else:
            if r.x_m is None or r.y_m is None:
                continue
            x, y = r.x_m, r.y_m
        heading = _line_heading(lines, i)
        if heading is None:
            heading = r.heading_deg
        if heading is None:
            continue
        w, h = camera.footprint_m(r.agl_m)
        out.append((i, footprint_polygon(x, y, heading, w, h)))
    return out


class OverlapResult:
    def __init__(self) -> None:
        self.group = CheckGroup(key="overlap", label="중복도·커버리지")
        self.coverage: Coverage | None = None
        self.forward_pct: list[float | None] = []
        self.gsd_cm: list[float | None] = []
        self.union: BaseGeometry | None = None
        """커버리지 합집합 (UTM). 조사 구역 추정이 쓴다."""


def analyze_overlap(
    rows: Sequence[CaptureRow],
    lines: Sequence[LineInfo],
    camera: CameraModel,
    cfg: OverlapConfig,
    epsg: int | None,
    line_spacing_m: float | None,
) -> OverlapResult:
    res = OverlapResult()
    n = len(rows)
    res.forward_pct = [None] * n
    res.gsd_cm = [None] * n
    survey = [ln for ln in lines if not ln.is_transit]
    line_of = {i: ln.index for ln in survey for i in range(ln.start_capture, ln.end_capture + 1)}
    for i, r in enumerate(rows):
        if r.agl_m is not None and r.agl_m > 0:
            res.gsd_cm[i] = round(camera.gsd_m(r.agl_m) * 100.0, 2)
    # 라인 방향으로 사각형을 놓는다 (라인 첫 캡처의 진입 구간은 전환 방향이라 쓰지 않는다)
    fps = footprints(rows, lines, camera)
    polys: list[Polygon] = [poly for _, poly in fps]
    widths: list[float] = []
    for i, _poly in fps:
        r = rows[i]
        assert r.agl_m is not None and r.x_m is not None and r.y_m is not None
        w, h = camera.footprint_m(r.agl_m)
        widths.append(w)
        # 종중복: 같은 라인의 직전 캡처
        prev = i - 1
        if prev >= 0 and line_of.get(prev) == line_of[i]:
            p = rows[prev]
            if p.x_m is not None and p.y_m is not None:
                d = math.hypot(r.x_m - p.x_m, r.y_m - p.y_m)
                res.forward_pct[i] = round(max(0.0, (h - d) / h * 100.0), 1)

    metrics: list[Metric] = []
    fwd = [v for v in res.forward_pct if v is not None]
    fwd_med = statistics.median(fwd) if fwd else None
    ok_pct = (sum(1 for v in fwd if v >= cfg.forward_warn_pct) / len(fwd) * 100.0) if fwd else None
    st = grade_min(fwd_med, cfg.forward_warn_pct, cfg.forward_fail_pct)
    if ok_pct is not None and ok_pct < cfg.pairs_ok_min_pct and st is Status.PASS:
        st = Status.WARN
    metrics.append(
        Metric(
            key="overlap.forward",
            label="종중복도 (실측 중앙값)",
            value=round(fwd_med, 1) if fwd_med is not None else None,
            unit="%",
            threshold=(
                f"WARN < {cfg.forward_warn_pct:.0f}%, FAIL < {cfg.forward_fail_pct:.0f}% ; "
                f"기준 충족 쌍 ≥ {cfg.pairs_ok_min_pct:.0f}%"
            ),
            status=st,
            reason=(
                f"연속 캡처 쌍 {len(fwd)}개, 최소 {fmt(min(fwd), 1, '%')}, "
                f"기준 충족 {fmt(ok_pct, 1, '%')}; 카메라 along-track 길이 × 실제 AGL 기준"
                if fwd
                else "AGL 또는 좌표가 없어 계산 불가"
            ),
            next_action=(
                "부족 구간이 특정 라인에 몰리면 그 라인 재비행 · "
                "전체적으로 낮으면 캡처 간격·속도 재설정\n"
                "영향: 종중복 부족 구간(captures.csv 의 forward_overlap_pct)은 정렬 실패·공백 위험"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            gate=True,
        )
    )
    w_mean = statistics.fmean(widths) if widths else None
    side = (
        (w_mean - line_spacing_m) / w_mean * 100.0
        if w_mean and line_spacing_m is not None
        else None
    )
    st = grade_min(side, cfg.side_warn_pct, cfg.side_fail_pct)
    metrics.append(
        Metric(
            key="overlap.side",
            label="횡중복도 (실측 라인 간격 기준)",
            value=round(side, 1) if side is not None else None,
            unit="%",
            threshold=f"WARN < {cfg.side_warn_pct:.0f}%, FAIL < {cfg.side_fail_pct:.0f}%",
            status=st,
            reason=(
                f"라인 간격 {fmt(line_spacing_m, 1, 'm')} vs across-track 폭 {fmt(w_mean, 1, 'm')}"
                if side is not None
                else "라인이 2개 미만이거나 AGL 없음"
            ),
            next_action=(
                "라인 간격을 좁혀 재비행 검토\n영향: 횡중복 부족 시 라인 사이 정렬 불량·공백"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )

    # 커버리지 합집합
    if polys:
        union = unary_union(polys)
        res.union = union
        geoms: list[Polygon] = (
            [g for g in union.geoms if isinstance(g, Polygon)]
            if isinstance(union, MultiPolygon)
            else ([union] if isinstance(union, Polygon) else [])
        )
        holes: list[Polygon] = [
            Polygon(ring)
            for g in geoms
            for ring in g.interiors
            if Polygon(ring).area >= cfg.hole_min_area_m2
        ]
        centroids: list[list[float]] = []
        geojson: dict[str, Any] | None = None
        if epsg is not None:
            tr = Transformer.from_crs(epsg, 4326, always_xy=True)
            for hpoly in holes:
                lon, lat = tr.transform(hpoly.centroid.x, hpoly.centroid.y)
                centroids.append([round(float(lon), 7), round(float(lat), 7)])
            wgs = shp_transform(tr.transform, union.simplify(0.5))
            geojson = dict(mapping(wgs))
        simp = union.simplify(0.5)
        simp_geoms: list[Polygon] = (
            [g for g in simp.geoms if isinstance(g, Polygon)]
            if isinstance(simp, MultiPolygon)
            else ([simp] if isinstance(simp, Polygon) else [])
        )
        rings = [[[round(x, 1), round(y, 1)] for x, y in g.exterior.coords] for g in simp_geoms]
        hole_rings = [[[round(x, 1), round(y, 1)] for x, y in hp.exterior.coords] for hp in holes]
        res.coverage = Coverage(
            area_m2=round(float(union.area), 1),
            n_holes=len(holes),
            holes_area_m2=round(sum(hp.area for hp in holes), 1),
            hole_centroids=centroids,
            geojson=geojson,
            epsg=epsg,
            rings_m=rings,
            hole_rings_m=hole_rings,
        )
        metrics.append(
            info_metric(
                "overlap.area",
                "조사 면적 (커버리지 합집합)",
                round(union.area / 10_000.0, 2),
                "ha",
                f"조사 라인 캡처 {len(polys)}개의 지상 커버리지 합집합 (실제 AGL 기준)",
            )
        )
        st = Status.PASS if not holes else Status.WARN
        metrics.append(
            Metric(
                key="overlap.holes",
                label="커버리지 공백",
                value=len(holes),
                unit="개",
                threshold=f"면적 ≥ {cfg.hole_min_area_m2:.0f} m² 인 내부 공백 ; WARN ≥ 1개",
                status=st,
                reason=(
                    f"총 {res.coverage.holes_area_m2:,.0f} m², 위치는 coverage.geojson 참고"
                    if holes
                    else "커버리지 내부에 빈 구간 없음"
                ),
                next_action=(
                    "해당 위치 보충 비행\n영향: 공백 위치에서 정사영상 단절" if holes else None
                ),
            )
        )
    else:
        metrics.append(
            Metric(
                key="overlap.holes",
                label="커버리지 공백",
                status=Status.NA,
                reason="커버리지를 계산할 캡처가 없음 (AGL·좌표 필요)",
            )
        )
    gsd = [v for v in res.gsd_cm if v is not None]
    metrics.append(
        info_metric(
            "overlap.gsd",
            "GSD (실측 AGL 기준 중앙값)",
            round(statistics.median(gsd), 2) if gsd else None,
            "cm/px",
            f"범위 {fmt(min(gsd), 2)}–{fmt(max(gsd), 2)} cm/px" if gsd else "AGL 없음",
        )
    )
    res.group = CheckGroup(key="overlap", label="중복도·커버리지", metrics=metrics)
    return res


def _line_heading(lines: Sequence[LineInfo], i: int) -> float | None:
    for ln in lines:
        if ln.start_capture <= i <= ln.end_capture and not math.isnan(ln.heading_deg):
            return ln.heading_deg
    return None
