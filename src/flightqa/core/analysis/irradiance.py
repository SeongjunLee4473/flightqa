"""§4.6 조도·태양 조건.

- 조도: RedEdge 는 DLS HorizontalIrradiance(밴드 평균), M3M 은 노출·게인 정규화 카운트. 절대값이
  아니라 비행 중 **상대 변동** 을 본다: 변동계수, 이동 중앙값 대비 급락(구름), 안정 구간 비율,
  전/후 대칭.
- 태양: ``pvlib`` 로 캡처 시각·좌표의 태양고도·방위각을 계산한다. RedEdge XMP 의 SolarElevation 과
  교차검증하고, 설정 하한(기본 30°) 미만 캡처 비율을 판정한다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence

import pandas as pd

from flightqa.config.schema import IrradianceConfig
from flightqa.core.models import CaptureMeta, CheckGroup, Metric, Status
from flightqa.core.rules import fmt, grade_max, grade_min, info_metric


class IrradianceResult:
    def __init__(self) -> None:
        self.group = CheckGroup(key="irradiance", label="조도·태양 조건")
        self.solar_elevation_calc: list[float | None] = []
        self.solar_azimuth_calc: list[float | None] = []
        self.event_indices: list[int] = []


def solar_positions(
    captures: Sequence[CaptureMeta],
) -> tuple[list[float | None], list[float | None]]:
    """pvlib 겉보기 태양고도·방위각 (도). 시각이나 좌표가 없으면 None."""
    n = len(captures)
    elev: list[float | None] = [None] * n
    az: list[float | None] = [None] * n
    idx = [
        i
        for i, c in enumerate(captures)
        if c.time_utc is not None and c.lat is not None and c.lon is not None
    ]
    if not idx:
        return elev, az
    times = pd.DatetimeIndex([captures[i].time_utc for i in idx])  # aware → UTC 유지
    lats = [captures[i].lat for i in idx]
    lons = [captures[i].lon for i in idx]
    from pvlib import solarposition  # scipy 포함 2 s — 처음 쓸 때 불러온다 (앱 시작 시간)

    pos = solarposition.get_solarposition(times, lats, lons)
    for k, i in enumerate(idx):
        elev[i] = float(pos["apparent_elevation"].iloc[k])
        az[i] = float(pos["azimuth"].iloc[k])
    return elev, az


def _moving_median(values: Sequence[float], window: int) -> list[float]:
    half = max(1, window // 2)
    out: list[float] = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append(statistics.median(values[lo:hi]))
    return out


def analyze_irradiance(
    captures: Sequence[CaptureMeta],
    cfg: IrradianceConfig,
    survey_indices: Sequence[int],
    has_xmp_solar: bool,
) -> IrradianceResult:
    res = IrradianceResult()
    res.solar_elevation_calc, res.solar_azimuth_calc = solar_positions(captures)
    metrics: list[Metric] = []
    survey = list(survey_indices) if survey_indices else list(range(len(captures)))

    # --- 조도 시계열 (조사 캡처만)
    series = [(i, captures[i].mean_irradiance()) for i in survey]
    vals = [(i, v) for i, v in series if v is not None and v > 0]
    irr = [v for _, v in vals]
    cv = (statistics.pstdev(irr) / statistics.fmean(irr) * 100.0) if len(irr) > 1 else None
    st = grade_max(cv, cfg.cv_warn_pct, cfg.cv_fail_pct)
    metrics.append(
        Metric(
            key="irradiance.cv",
            label="조도 변동계수 (조사 구간)",
            value=round(cv, 1) if cv is not None else None,
            unit="%",
            threshold=f"WARN ≥ {cfg.cv_warn_pct:.0f}%, FAIL ≥ {cfg.cv_fail_pct:.0f}%",
            status=st,
            reason=(
                f"캡처 {len(irr)}개, 평균 {fmt(statistics.fmean(irr), 2)}, "
                f"최소/최대 {fmt(min(irr), 2)}/{fmt(max(irr), 2)} (밴드 평균 조도)"
                if irr
                else "조도 태그 없음"
            ),
            next_action=(
                "DLS 보정 적용 · 구름 통과 구간은 재비행 검토\n"
                "영향: 조도 변동이 크면 밴드 간·라인 간 반사율 불일치"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )

    # --- 급변 구간 (이동 중앙값 대비 급락) 와 안정 구간
    events: list[int] = []
    stable = 0
    if len(irr) >= 3:
        mm = _moving_median(irr, cfg.drop_window)
        band = cfg.stable_band_pct / 100.0
        in_event = False
        for k, (i, v) in enumerate(vals):
            ratio = v / mm[k] if mm[k] > 0 else 1.0
            if ratio < cfg.drop_factor:
                if not in_event:
                    events.append(i)
                in_event = True
            else:
                in_event = False
            if abs(ratio - 1.0) <= band:
                stable += 1
    res.event_indices = events
    st = grade_max(float(len(events)), float(cfg.events_warn_count), float(cfg.events_fail_count))
    metrics.append(
        Metric(
            key="irradiance.events",
            label="조도 급변 구간 (구름 통과 의심)",
            value=len(events) if irr else None,
            unit="회",
            threshold=(
                f"이동 중앙값({cfg.drop_window}캡처) 의 {cfg.drop_factor:.0%} 미만으로 하락 ; "
                f"WARN ≥ {cfg.events_warn_count}회, FAIL ≥ {cfg.events_fail_count}회"
            ),
            status=st if irr else Status.NA,
            reason=(f"시작 캡처 인덱스 {events[:10]}" if events else "급락 없음")
            if irr
            else "조도 태그 없음",
            next_action=(
                "해당 라인 재비행 (우선순위 높음)\n영향: 급변 구간 캡처는 조도 보정 후에도 잔차 큼"
                if st in (Status.WARN, Status.FAIL) and irr
                else None
            ),
            details={"indices": events[:100]},
        )
    )
    stable_pct = (stable / len(irr) * 100.0) if irr else None
    st = grade_min(stable_pct, cfg.stable_fraction_warn_pct, float("-inf"))
    metrics.append(
        Metric(
            key="irradiance.stable_fraction",
            label="조도 안정 구간 비율",
            value=round(stable_pct, 1) if stable_pct is not None else None,
            unit="%",
            threshold=(
                f"이동 중앙값 ±{cfg.stable_band_pct:.0f}% 이내 ; "
                f"WARN < {cfg.stable_fraction_warn_pct:.0f}%"
            ),
            status=st,
            reason=f"{stable}/{len(irr)} 캡처" if irr else "조도 태그 없음",
            next_action=(
                "맑은 날 재비행 검토\n영향: 불안정 구간이 넓으면 반사율 정확도 전반 저하"
                if st is Status.WARN
                else None
            ),
        )
    )

    # --- 전/후 대칭 (처음 10% vs 마지막 10%)
    sym: float | None = None
    if len(irr) >= 10:
        k = max(1, len(irr) // 10)
        a, b = statistics.fmean(irr[:k]), statistics.fmean(irr[-k:])
        sym = abs(a - b) / max(a, b) * 100.0 if max(a, b) > 0 else None
    st = grade_max(sym, cfg.symmetry_warn_pct, float("inf"))
    metrics.append(
        Metric(
            key="irradiance.symmetry",
            label="비행 전/후 조도 비대칭",
            value=round(sym, 1) if sym is not None else None,
            unit="%",
            threshold=f"처음 10% vs 마지막 10% 평균 차 ; WARN ≥ {cfg.symmetry_warn_pct:.0f}%",
            status=st,
            reason="비행 중 조도 추세 (오전 상승·오후 하강·구름 유입)"
            if sym is not None
            else "캡처 부족",
            next_action=(
                "비행 전·후 패널 촬영으로 시간 보간 보정 · 패널이 없으면 DLS 보정에 의존"
                if st is Status.WARN
                else None
            ),
        )
    )

    # --- 태양고도
    el = _floats(res.solar_elevation_calc[i] for i in survey)
    azs = _floats(res.solar_azimuth_calc[i] for i in survey)
    low = [v for v in el if v < cfg.solar_elevation_min_deg]
    pct_low = (len(low) / len(el) * 100.0) if el else None
    st = grade_max(pct_low, cfg.low_sun_warn_pct, cfg.low_sun_fail_pct)
    metrics.append(
        Metric(
            key="irradiance.solar_elevation",
            label="태양고도 미달 캡처 비율",
            value=round(pct_low, 1) if pct_low is not None else None,
            unit="%",
            threshold=(
                f"태양고도 < {cfg.solar_elevation_min_deg:.0f}° ; "
                f"WARN ≥ {cfg.low_sun_warn_pct:.0f}%, FAIL ≥ {cfg.low_sun_fail_pct:.0f}%"
            ),
            status=st,
            reason=(
                f"pvlib 계산 태양고도 {fmt(min(el), 1, '°')}–{fmt(max(el), 1, '°')} "
                f"(중앙 {fmt(statistics.median(el), 1, '°')}), 방위 "
                f"{fmt(statistics.median(azs) if azs else None, 0, '°')}"
                if el
                else "시각·좌표 없음"
            ),
            next_action=(
                "정오 ±2시간 재비행 권장\n영향: 낮은 태양고도는 그림자·BRDF 효과 증가"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            details={"min_deg": round(min(el), 2) if el else None},
        )
    )

    # --- XMP 기록 태양고도 교차검증 (RedEdge DLS)
    if has_xmp_solar:
        diffs = [
            abs(captures[i].solar_elevation_deg - res.solar_elevation_calc[i])  # type: ignore[operator]
            for i in survey
            if captures[i].solar_elevation_deg is not None
            and res.solar_elevation_calc[i] is not None
        ]
        med = statistics.median(diffs) if diffs else None
        st = grade_max(med, cfg.xmp_solar_mismatch_warn_deg, float("inf"))
        metrics.append(
            Metric(
                key="irradiance.xmp_solar_check",
                label="DLS 태양고도 기록–계산값 차이",
                value=round(med, 2) if med is not None else None,
                unit="°",
                threshold=f"중앙 차이 WARN ≥ {cfg.xmp_solar_mismatch_warn_deg:.0f}°",
                status=st,
                reason=(
                    f"{len(diffs)} 캡처, 최대 차이 {fmt(max(diffs), 2, '°')}"
                    if diffs
                    else "XMP 태양고도 없음"
                ),
                next_action=(
                    "카메라 시각 동기 확인\n원인: DLS 시각·GPS 또는 자세 기록 오류 가능성"
                    if st is Status.WARN
                    else None
                ),
            )
        )
    else:
        metrics.append(
            info_metric(
                "irradiance.xmp_solar_check",
                "DLS 태양고도 기록–계산값 차이",
                None,
                "°",
                "이 센서는 태양고도를 기록하지 않음 (pvlib 계산값만 사용)",
            )
        )
    res.group = CheckGroup(key="irradiance", label="조도·태양 조건", metrics=metrics)
    return res


def _floats(values: Iterable[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and not math.isnan(v)]
