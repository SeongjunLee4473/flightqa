"""§4.7 캘리브레이션 패널.

- 지상 캡처: AGL ≤ ``ground_agl_max_m`` (AGL 을 모르면 고도가 비행 최저고도 근처). 비행 시작 전
  ``window_min`` 안에 있으면 '전 패널', 종료 후면 '후 패널' 후보.
- 패널 여부는 픽셀 통계의 밝은 균일 사각 영역 힌트로 "패널로 추정" 만 표시한다 (정밀 검출은 비범위).
- 패널 캡처의 포화는 치명적이므로 따로 판정한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from flightqa.config.schema import ExposureConfig, PanelConfig
from flightqa.core.models import (
    CaptureMeta,
    CheckGroup,
    LineInfo,
    Metric,
    PanelInfo,
    SensorKind,
    Status,
)


def ground_flags(
    captures: Sequence[CaptureMeta], agl: Sequence[float | None], cfg: PanelConfig
) -> list[bool]:
    """캡처별 지상 여부."""
    n = len(captures)
    known = [a for a in agl if a is not None]
    if known:
        return [a is not None and a <= cfg.ground_agl_max_m for a in agl]
    alts = [c.alt_msl_m for c in captures if c.alt_msl_m is not None]
    if not alts:
        return [False] * n
    floor = min(alts)
    return [
        c.alt_msl_m is not None and c.alt_msl_m - floor <= cfg.ground_agl_max_m for c in captures
    ]


def analyze_panel(
    captures: Sequence[CaptureMeta],
    is_ground: Sequence[bool],
    lines: Sequence[LineInfo],
    cfg: PanelConfig,
    exp_cfg: ExposureConfig,
    stats: Mapping[tuple[int, str], dict[str, Any]],
    spectral_bands: Sequence[str],
    sensor_kind: SensorKind = SensorKind.REDEDGE_MX,
) -> tuple[CheckGroup, PanelInfo]:
    info = PanelInfo()
    required = cfg.required(sensor_kind.value)
    survey = [ln for ln in lines if not ln.is_transit]
    first = min((ln.start_capture for ln in survey), default=None)
    last = max((ln.end_capture for ln in survey), default=None)
    window = timedelta(minutes=cfg.window_min)
    t_first = captures[first].time_utc if first is not None else None
    t_last = captures[last].time_utc if last is not None else None
    for i, c in enumerate(captures):
        if not is_ground[i] or c.time_utc is None:
            continue
        if first is not None and i < first and t_first and t_first - c.time_utc <= window:
            info.pre_indices.append(i)
        elif last is not None and i > last and t_last and c.time_utc - t_last <= window:
            info.post_indices.append(i)
    candidates = info.pre_indices + info.post_indices

    # 패널 힌트·포화 (지상 캡처의 픽셀 통계)
    for i in candidates:
        band_stats: list[dict[str, Any]] = []
        for b in spectral_bands:
            ps = stats.get((i, b))
            if ps is not None and "error" not in ps:
                band_stats.append(ps)
        if not band_stats:
            continue
        if any(float(s["sat_pct"]) >= exp_cfg.saturated_pixel_warn_pct for s in band_stats):
            info.saturated_indices.append(i)
        hints = [s for s in band_stats if "bright_frac" in s]
        if hints and cfg.detect_bright_region:
            votes = sum(
                1
                for s in hints
                if cfg.bright_fraction_min <= s["bright_frac"] <= cfg.bright_fraction_max
                and s["bright_fill"] >= 0.6
                and s["bright_cv"] <= 0.15
            )
            if votes >= max(1, len(hints) // 2):
                info.panel_like_indices.append(i)

    metrics: list[Metric] = []
    n_pre, n_post = len(info.pre_indices), len(info.post_indices)
    n_pre_like = len([i for i in info.panel_like_indices if i in info.pre_indices])
    if n_pre:
        st_pre = Status.PASS
    elif required:
        st_pre = Status.FAIL
    else:
        st_pre = Status.NA
    metrics.append(
        Metric(
            key="panel.pre",
            label="비행 전 패널 촬영",
            value=n_pre,
            unit="캡처",
            threshold=(
                f"첫 조사 라인 전 {cfg.window_min:.0f}분 내 "
                f"지상(AGL ≤ {cfg.ground_agl_max_m:.0f} m) 캡처 ; "
                + ("없으면 FAIL (게이트)" if required else "선택 항목 (없으면 N/A)")
            ),
            status=st_pre,
            reason=(
                f"지상 캡처 인덱스 {info.pre_indices[:10]}"
                + (f", 패널로 추정 {n_pre_like}개" if cfg.detect_bright_region else "")
                if n_pre
                else (
                    "비행 전 지상 캡처 없음 — 카메라가 이륙 후 촬영을 시작했거나 패널을 찍지 않음"
                    if required
                    else "지상 캡처 없음. 이 센서는 패널이 선택 항목 (조도 센서 보정)"
                )
            ),
            next_action=(
                "패널 촬영 파일이 다른 폴더에 있으면 함께 지정 · 없으면 재촬영 시 반드시 촬영\n"
                "영향: 반사율 변환 기준 부재 · DLS 조도만으로 보정 시 절대 반사율 오차 증가"
                if st_pre is Status.FAIL
                else None
            ),
            details={"indices": info.pre_indices, "panel_like": info.panel_like_indices},
            gate=required,
        )
    )
    st_post = Status.PASS if n_post else (Status.WARN if required else Status.NA)
    metrics.append(
        Metric(
            key="panel.post",
            label="비행 후 패널 촬영",
            value=n_post,
            unit="캡처",
            threshold=(
                f"마지막 조사 라인 후 {cfg.window_min:.0f}분 내 지상 캡처 ; "
                + ("없으면 WARN" if required else "선택 항목")
            ),
            status=st_post,
            reason=(
                f"지상 캡처 인덱스 {info.post_indices[:10]}" if n_post else "비행 후 지상 캡처 없음"
            ),
            next_action=(
                "다음 비행부터 비행 후 패널 촬영 · 조도 비대칭 항목이 주의면 영향 큼\n"
                "영향: 비행 중 조도 변화를 비행 전 패널 하나로만 보정"
                if st_post is Status.WARN
                else None
            ),
            details={"indices": info.post_indices},
        )
    )
    n_sat = len(info.saturated_indices)
    st_sat = Status.FAIL if n_sat else (Status.PASS if candidates else Status.NA)
    metrics.append(
        Metric(
            key="panel.saturation",
            label="패널 캡처 포화",
            value=n_sat if candidates else None,
            unit="캡처",
            threshold=(
                f"패널 후보 캡처의 어느 밴드라도 포화 픽셀 ≥ {exp_cfg.saturated_pixel_warn_pct}% ; "
                "FAIL"
            ),
            status=st_sat,
            reason=(
                f"포화된 패널 후보 {info.saturated_indices[:10]}"
                if n_sat
                else ("패널 후보 캡처 모두 포화 없음" if candidates else "패널 후보 없음")
            ),
            next_action=(
                "포화 없는 다른 패널 캡처 선택 · "
                "없으면 절대 반사율이 DLS 만으로 보정됨을 보고서에 명시\n"
                "영향: 포화된 패널 영상은 반사율 기준으로 사용 불가"
                if n_sat
                else None
            ),
            details={"indices": info.saturated_indices},
            gate=True,
        )
    )
    return CheckGroup(key="panel", label="캘리브레이션 패널", metrics=metrics), info
