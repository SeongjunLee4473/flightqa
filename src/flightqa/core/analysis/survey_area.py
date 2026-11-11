"""조사 구역: 임무 파일(KMZ) 폴리곤 또는 궤적 기반 추정.

조정기(DJI Pilot 2 / RC)에서 그린 임무 폴리곤은 영상 EXIF/XMP 어디에도 기록되지 않는다
(docs/ASSUMPTIONS.md 2026-09-30). 두 경로가 있다.

1. **임무 파일** (``method = "mission_kmz"``): 조정기에서 내보낸 KMZ 의 매핑 폴리곤을 그대로 쓴다
   (``core/mission.py``). 이때는 폴리곤 밖 캡처 비율도 낸다 — 라인 끝은 임무 ``margin`` 만큼
   폴리곤 밖으로 나가므로 정보 항목이다.
2. **궤적 추정** (``method = "trajectory_hull"``): 조사 라인 캡처점(전환 구간·지상 캡처 제외)의
   오목 껍질(``shapely.concave_hull(ratio)``) 에 여유 폭을 더한다. 점이 한 줄이면 볼록 껍질(선) 로
   폴백하고 버퍼가 면을 만든다. 여유 폭은 캡처 지상 커버리지의 짧은 변 × 비율이며 기본 0 이다:
   실제 Pilot 2 매핑 임무(2026-09-29, 12 라인) 에서 점 껍질과 임무 폴리곤의 IoU 가 여유 0 m 에서
   0.88 로 최대였고 여유를 줄수록 떨어졌다 (바깥 라인이 폴리곤 경계에 놓임). 여유가 0 이면
   충족률은 사실상 내부 공백만 잡는다.

충족률 = (커버리지 합집합 ∩ 구역) / 구역. 미촬영 조각(구역 − 커버리지)은 GIS 로 내보낸다.
추정치는 보고서·내보내기 모두 "추정" 으로 표기한다.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

from pyproj import Transformer
from shapely import concave_hull
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from flightqa.config.schema import SurveyAreaConfig
from flightqa.core.models import CameraModel, CaptureRow, LineInfo, Metric, Status, SurveyArea
from flightqa.core.rules import fmt, grade_min, info_metric

METHOD_MISSION = "mission_kmz"
METHOD_HULL = "trajectory_hull"


class SurveyAreaResult:
    def __init__(self) -> None:
        self.area: SurveyArea | None = None
        self.metrics: list[Metric] = []
        self.geometry: BaseGeometry | None = None
        """구역 (UTM). 래스터 내보내기용."""
        self.uncovered: BaseGeometry | None = None


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    if isinstance(geom, Polygon):
        return [] if geom.is_empty else [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    if hasattr(geom, "geoms"):
        out: list[Polygon] = []
        for g in geom.geoms:
            out.extend(_polygons(g))
        return out
    return []


def _hull(points: Sequence[tuple[float, float]], ratio: float) -> BaseGeometry:
    mp = MultiPoint(points)
    hull: BaseGeometry = concave_hull(mp, ratio=ratio)
    if hull.is_empty or not isinstance(hull, Polygon) or hull.area <= 0.0:
        # 점이 한 줄(라인 1개) 이거나 오목 껍질이 무너진 경우: 볼록 껍질 (선 또는 면)
        hull = mp.convex_hull
    return hull


def estimate_survey_area(
    rows: Sequence[CaptureRow],
    lines: Sequence[LineInfo],
    is_ground: Sequence[bool],
    camera: CameraModel,
    coverage: BaseGeometry | None,
    cfg: SurveyAreaConfig,
    epsg: int | None,
    mission: BaseGeometry | None = None,
    mission_source: str | None = None,
) -> SurveyAreaResult:
    """``mission`` (UTM, 비행과 같은 EPSG) 이 있으면 그것이 구역이고, 없으면 궤적에서 추정한다."""
    res = SurveyAreaResult()
    if not cfg.enabled:
        return res
    survey = [ln for ln in lines if not ln.is_transit]
    line_idx = {i for ln in survey for i in range(ln.start_capture, ln.end_capture + 1)}
    pts: list[tuple[float, float]] = []
    agls: list[float] = []
    for i, r in enumerate(rows):
        if i not in line_idx or (i < len(is_ground) and is_ground[i]):
            continue
        if r.x_m is None or r.y_m is None:
            continue
        pts.append((r.x_m, r.y_m))
        if r.agl_m is not None and r.agl_m > 0:
            agls.append(r.agl_m)

    use_mission = mission is not None and not mission.is_empty and mission.area > 0.0
    if use_mission:
        assert mission is not None
        area_geom: BaseGeometry = mission
        margin = 0.0
        method = METHOD_MISSION
        what = "임무 구역 (KMZ)"
    else:
        if len(pts) < cfg.min_points or not agls:
            res.metrics.append(
                Metric(
                    key="overlap.survey_fill",
                    label="추정 조사 구역 충족률",
                    status=Status.NA,
                    reason=(
                        f"조사 라인 캡처 {len(pts)}개 (최소 {cfg.min_points}) 또는 AGL 없음 — "
                        "구역을 추정할 수 없음. 조정기 임무 KMZ 를 주면 폴리곤을 그대로 쓴다"
                    ),
                )
            )
            return res
        w, h = camera.footprint_m(statistics.median(agls))
        margin = cfg.margin_m if cfg.margin_m > 0 else cfg.margin_frac * min(w, h)
        area_geom = _hull(pts, cfg.hull_ratio).buffer(margin, join_style="round").simplify(0.5)
        method = METHOD_HULL
        what = "추정 조사 구역"
    if area_geom.is_empty or area_geom.area <= 0.0:
        return res
    res.geometry = area_geom
    area_m2 = float(area_geom.area)

    fill: float | None = None
    covered_m2 = 0.0
    uncovered_parts: list[Polygon] = []
    if coverage is not None and not coverage.is_empty:
        covered_m2 = float(coverage.intersection(area_geom).area)
        fill = covered_m2 / area_m2 * 100.0
        uncov = area_geom.difference(coverage)
        uncovered_parts = [g for g in _polygons(uncov) if g.area >= cfg.uncovered_min_area_m2]
        res.uncovered = MultiPolygon(uncovered_parts) if uncovered_parts else None
    uncovered_m2 = float(sum(g.area for g in uncovered_parts))

    n_outside: int | None = None
    outside_pct: float | None = None
    if use_mission and pts:
        n_outside = sum(1 for x, y in pts if not area_geom.covers(Point(x, y)))
        outside_pct = n_outside / len(pts) * 100.0

    geojson: dict[str, Any] | None = None
    unc_geojson: dict[str, Any] | None = None
    bbox: list[float] | None = None
    if epsg is not None:
        tr = Transformer.from_crs(epsg, 4326, always_xy=True)
        wgs = shp_transform(tr.transform, area_geom)
        geojson = dict(mapping(wgs))
        bbox = [round(float(v), 7) for v in wgs.bounds]
        if res.uncovered is not None:
            unc_geojson = dict(mapping(shp_transform(tr.transform, res.uncovered)))
    rings = [
        [[round(x, 1), round(y, 1)] for x, y in g.exterior.coords] for g in _polygons(area_geom)
    ]
    res.area = SurveyArea(
        method=method,
        source=mission_source if use_mission else None,
        n_points=len(pts),
        hull_ratio=cfg.hull_ratio,
        margin_m=round(margin, 1),
        area_m2=round(area_m2, 1),
        covered_area_m2=round(covered_m2, 1),
        uncovered_area_m2=round(uncovered_m2, 1),
        fill_pct=round(fill, 1) if fill is not None else None,
        n_uncovered=len(uncovered_parts),
        n_outside=n_outside,
        outside_pct=round(outside_pct, 1) if outside_pct is not None else None,
        epsg=epsg,
        geojson=geojson,
        uncovered_geojson=unc_geojson,
        rings_m=rings,
        bbox=bbox,
    )

    if use_mission:
        area_reason = (
            f"조정기 임무 파일 {mission_source or 'KMZ'} 의 매핑 폴리곤 "
            f"(survey_area.geojson · flightqa.gpkg 의 mission_polygon)"
        )
    else:
        area_reason = (
            f"조사 라인 캡처 {len(pts)}개의 오목 껍질(ratio {cfg.hull_ratio:g}) + "
            f"여유 {margin:.0f} m. 조정기 임무 폴리곤이 아니라 궤적에서 추정한 값 "
            "(survey_area.geojson · flightqa.gpkg)"
        )
    res.metrics.append(
        info_metric(
            "overlap.survey_area",
            f"{what} 면적",
            round(area_m2 / 10_000.0, 2),
            "ha",
            area_reason,
        )
    )
    st = grade_min(fill, cfg.fill_warn_pct, cfg.fill_fail_pct)
    fix = (
        "미촬영 조각이 가장자리에 몰려 있으면 외곽 라인 1개 추가 · 안쪽이면 그 구간만 보충 비행\n"
        "위치: survey_area.geojson 의 uncovered · survey_area.tif 값 1"
    )
    if not use_mission:
        fix += (
            ". 조정기의 실제 임무 폴리곤과 대조하려면 Pilot 2 에서 임무 KMZ 를 내보내 "
            "데이터 폴더에 두거나 --mission 으로 지정"
        )
    res.metrics.append(
        Metric(
            key="overlap.survey_fill",
            label=f"{what} 충족률",
            value=round(fill, 1) if fill is not None else None,
            unit="%",
            threshold=f"WARN < {cfg.fill_warn_pct:.0f}%, FAIL < {cfg.fill_fail_pct:.0f}%",
            status=st,
            reason=(
                f"구역 {fmt(area_m2 / 10_000.0, 2, 'ha')} 중 커버리지 "
                f"{fmt(covered_m2 / 10_000.0, 2, 'ha')}, 미촬영 조각 {len(uncovered_parts)}개 "
                f"{fmt(uncovered_m2, 0, 'm²')} (≥ {cfg.uncovered_min_area_m2:.0f} m²)"
                if fill is not None
                else "커버리지 합집합이 없어 계산 불가"
            ),
            next_action=(
                f"{fix}\n영향: {what} 안 미촬영 {uncovered_m2:,.0f} m² "
                f"(조각 {len(uncovered_parts)}개)"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    if use_mission:
        res.metrics.append(
            info_metric(
                "overlap.survey_outside",
                "임무 폴리곤 밖 캡처 비율",
                round(outside_pct, 1) if outside_pct is not None else None,
                "%",
                (
                    f"조사 라인 캡처 {len(pts)}개 중 {n_outside}개가 폴리곤 밖. "
                    "라인 끝은 임무 margin 만큼 폴리곤 밖으로 나가므로 수 % 는 정상"
                    if outside_pct is not None
                    else "조사 라인 캡처 없음"
                ),
            )
        )
    return res
