"""§4.5 노출·포화.

픽셀 통계(``pixels.py``)와 밴드별 노출 태그로 캡처·밴드 단위 포화율, 저노출, 노출 급변을 판정한다.
지상(패널) 캡처는 조사 통계에서 제외하고 §4.7 에서 따로 본다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from flightqa.config.schema import ExposureConfig
from flightqa.core.models import CaptureMeta, CheckGroup, Metric, PixelRow, Status
from flightqa.core.rules import fmt, grade_max, info_metric
from flightqa.core.sensors.base import SensorPlugin

PixelStats = Mapping[tuple[int, str], dict[str, Any]]
"""(캡처 인덱스, 밴드 키) → compute_pixel_stats 결과."""


class ExposureResult:
    def __init__(self) -> None:
        self.group = CheckGroup(key="exposure", label="노출·포화")
        self.pixel_rows: list[PixelRow] = []
        self.band_hist: dict[str, list[int]] = {}
        self.hist_edges: list[int] = []
        self.sat_max_pct: list[float | None] = []
        self.underexposed: list[bool | None] = []
        self.saturated_indices: list[int] = []


def analyze_exposure(
    captures: Sequence[CaptureMeta],
    sensor: SensorPlugin,
    cfg: ExposureConfig,
    stats: PixelStats,
    is_ground: Sequence[bool],
) -> ExposureResult:
    res = ExposureResult()
    n = len(captures)
    res.sat_max_pct = [None] * n
    res.underexposed = [None] * n
    bands = sensor.spectral_band_keys
    dyn = float(sensor.camera.saturation_dn)
    hist: dict[str, list[int]] = {}
    stride_used = 1
    n_stats = 0
    errors: list[str] = []

    for i, c in enumerate(captures):
        sat_vals: list[float] = []
        under_flags: list[bool] = []
        for b in bands:
            ps = stats.get((i, b))
            if ps is None:
                continue
            if "error" in ps:
                errors.append(
                    f"{c.bands[b].path.name}: {ps['error']}" if b in c.bands else ps["error"]
                )
                continue
            n_stats += 1
            stride_used = max(stride_used, int(ps.get("stride", 1)))
            sat_vals.append(float(ps["sat_pct"]))
            under_flags.append((ps["p99"] - ps["p1"]) < dyn * cfg.underexposed_range_pct / 100.0)
            h = ps.get("hist")
            if h:
                acc = hist.setdefault(b, [0] * len(h))
                for k, v in enumerate(h):
                    acc[k] += v
            res.pixel_rows.append(
                PixelRow(
                    index=i,
                    capture_id=c.capture_id,
                    band=b,
                    file=c.bands[b].path.name if b in c.bands else "",
                    sat_pct=round(float(ps["sat_pct"]), 4),
                    p1=float(ps["p1"]),
                    p99=float(ps["p99"]),
                    mean=round(float(ps["mean"]), 1),
                    n_pixels=int(ps["n"]),
                    stride=int(ps.get("stride", 1)),
                )
            )
        if sat_vals:
            res.sat_max_pct[i] = round(max(sat_vals), 4)
            res.underexposed[i] = all(under_flags)
    res.band_hist = hist
    if hist:
        nb = len(next(iter(hist.values())))
        res.hist_edges = [round(k * 65536 / nb) for k in range(nb + 1)]

    survey = [i for i in range(n) if not is_ground[i]]
    metrics: list[Metric] = []

    # 1. 포화 캡처 비율
    with_stats = [i for i in survey if res.sat_max_pct[i] is not None]
    sat_idx = [i for i in with_stats if (res.sat_max_pct[i] or 0.0) >= cfg.saturated_pixel_warn_pct]
    severe = [i for i in with_stats if (res.sat_max_pct[i] or 0.0) >= cfg.saturated_pixel_fail_pct]
    res.saturated_indices = sat_idx
    pct = (len(sat_idx) / len(with_stats) * 100.0) if with_stats else None
    st = grade_max(pct, cfg.saturated_captures_warn_pct, cfg.saturated_captures_fail_pct)
    by_band: dict[str, int] = {}
    for r in res.pixel_rows:
        if not is_ground[r.index] and r.sat_pct >= cfg.saturated_pixel_warn_pct:
            by_band[r.band] = by_band.get(r.band, 0) + 1
    metrics.append(
        Metric(
            key="exposure.saturated_captures",
            label="포화 캡처 비율",
            value=round(pct, 2) if pct is not None else None,
            unit="%",
            threshold=(
                f"캡처 포화 = 한 밴드라도 포화 픽셀 ≥ {cfg.saturated_pixel_warn_pct}% ; "
                f"WARN ≥ {cfg.saturated_captures_warn_pct}%, "
                f"FAIL ≥ {cfg.saturated_captures_fail_pct}%"
            ),
            status=st,
            reason=(
                f"{len(sat_idx)}/{len(with_stats)} 캡처 (심각 ≥ {cfg.saturated_pixel_fail_pct}%: "
                f"{len(severe)}개; 밴드별 {by_band}); 포화 DN {sensor.camera.saturation_dn}, "
                f"샘플링 stride {stride_used}"
                if with_stats
                else "픽셀 통계 없음"
            ),
            next_action=(
                "포화 캡처가 몰린 라인은 노출 설정을 낮춰 재비행 · "
                "산발적이면 후처리에서 해당 영상 가중치 하향\n"
                "영향: 포화 픽셀은 반사율 변환에서 잘려 "
                "밝은 지물(모래·건물·수면 반사) 구간 값 신뢰 불가"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            details={"indices": sat_idx[:200], "by_band": by_band, "n_severe": len(severe)},
            gate=True,
        )
    )

    # 2. 저노출
    under = [i for i in with_stats if res.underexposed[i]]
    pct_u = (len(under) / len(with_stats) * 100.0) if with_stats else None
    st = grade_max(pct_u, cfg.underexposed_captures_warn_pct, float("inf"))
    metrics.append(
        Metric(
            key="exposure.underexposed",
            label="저노출 캡처 비율",
            value=round(pct_u, 2) if pct_u is not None else None,
            unit="%",
            threshold=(
                f"p99−p1 < 다이내믹 레인지의 {cfg.underexposed_range_pct}% (모든 밴드) ; "
                f"WARN ≥ {cfg.underexposed_captures_warn_pct}%"
            ),
            status=st,
            reason=f"{len(under)}/{len(with_stats)} 캡처" if with_stats else "픽셀 통계 없음",
            next_action=(
                "노출 자동 설정 확인\n영향: 저노출 영상은 양자화 잡음이 커 반사율 정밀도 저하"
                if st is Status.WARN
                else None
            ),
            details={"indices": under[:200]},
        )
    )

    # 3. 노출 급변 (밴드별 노출시간, 연속 조사 캡처)
    jumps: list[dict[str, Any]] = []
    for b in bands:
        prev: float | None = None
        prev_i: int | None = None
        for i in survey:
            bm = captures[i].bands.get(b)
            e = bm.exposure.exposure_s if bm else None
            if e is None or e <= 0:
                continue
            if prev is not None and prev_i == i - 1:
                ratio = max(e / prev, prev / e)
                if ratio >= cfg.exposure_jump_factor:
                    jumps.append({"index": i, "band": b, "ratio": round(ratio, 2)})
            prev, prev_i = e, i
    st = grade_max(float(len(jumps)), float(cfg.exposure_jumps_warn_count), float("inf"))
    metrics.append(
        Metric(
            key="exposure.jumps",
            label="노출시간 급변",
            value=len(jumps),
            unit="회",
            threshold=(
                f"연속 캡처 간 ≥ {cfg.exposure_jump_factor}배 ; "
                f"WARN ≥ {cfg.exposure_jumps_warn_count}회"
            ),
            status=st,
            reason=("밴드별 노출시간 시계열에서 급변 " + (f"{len(jumps)}회" if jumps else "없음")),
            next_action=(
                "조도 항목과 대조 · 반사율 보정에 DLS 조도 적용 여부 확인\n"
                "원인: 구름 통과 또는 밝은 지물 진입"
                if st is Status.WARN
                else None
            ),
            details={"jumps": jumps[:100]},
        )
    )

    # 4. 노출 요약 정보
    exp_summary: dict[str, str] = {}
    for b in bands:
        es = _floats(
            captures[i].bands[b].exposure.exposure_s for i in survey if b in captures[i].bands
        )
        isos = _floats(captures[i].bands[b].exposure.iso for i in survey if b in captures[i].bands)
        if es:
            exp_summary[b] = (
                f"1/{1 / statistics.median(es):.0f}s "
                f"({fmt(1000 * min(es), 2)}–{fmt(1000 * max(es), 2)} ms)"
                + (f", ISO {statistics.median(isos):.0f}" if isos else "")
            )
    metrics.append(
        info_metric(
            "exposure.summary",
            "노출 설정 (밴드별 중앙값)",
            len(exp_summary) or None,
            "밴드",
            "; ".join(f"{k}: {v}" for k, v in exp_summary.items())
            if exp_summary
            else "노출 태그 없음",
        )
    )
    if errors:
        metrics.append(
            Metric(
                key="exposure.read_errors",
                label="픽셀 읽기 실패",
                value=len(errors),
                unit="개",
                threshold="WARN ≥ 1개",
                status=Status.WARN,
                reason="; ".join(errors[:3]),
                next_action="원본 카드에서 재복사\n원인: 파일 손상 가능성",
                details={"errors": errors[:50]},
            )
        )
    res.group = CheckGroup(key="exposure", label="노출·포화", metrics=metrics)
    return res


def _floats(values: Iterable[float | None]) -> list[float]:
    return [float(v) for v in values if v is not None and v > 0]


def cv_pct(values: Sequence[float]) -> float | None:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if len(vals) < 2:
        return None
    m = statistics.fmean(vals)
    return statistics.pstdev(vals) / m * 100.0 if m else None
