"""§4.3 비행 궤적·고도·속도·연직 이탈각.

- 좌표는 UTM (pyproj) 으로 투영해 m 단위로 계산한다.
- 라인 분리: 진행방향이 ``line_heading_break_deg`` 이상 꺾이거나 시간 공백이 크면 새 라인.
  ``min_captures_per_line`` 미만은 전환 구간(transit).
- AGL: M3M 은 RelativeAltitude(이륙점 기준). RedEdge 는 GPS 고도 − 이륙점 고도(설정 또는 첫 캡처).
  산림지는 수관 높이 보정이 불가하므로 보고서에 '이륙점 기준' 임을 명시한다 (CLAUDE.md §4.3).

내부 계산은 결측을 NaN 으로 두는 float 배열로 하고, 모델에 넣을 때만 None 으로 바꾼다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from itertools import pairwise

from pyproj import Transformer

from flightqa.config.schema import FlightConfig, RtkConfig
from flightqa.core.analysis.dem import DemSampler
from flightqa.core.models import (
    CaptureMeta,
    CaptureRow,
    CheckGroup,
    LineInfo,
    Metric,
    MissionPlan,
    SensorKind,
    Status,
)
from flightqa.core.rules import fmt, grade_deviation_pct, grade_max, grade_min, info_metric

NAN = float("nan")


def _ok(v: float) -> bool:
    return not math.isnan(v)


def _opt(v: float, digits: int | None = None) -> float | None:
    if math.isnan(v):
        return None
    return round(v, digits) if digits is not None else v


def _vals(xs: Sequence[float]) -> list[float]:
    return [x for x in xs if _ok(x)]


def utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def project(captures: Sequence[CaptureMeta]) -> tuple[list[float], list[float], int | None]:
    """위경도 → UTM (m). 좌표 없는 캡처는 NaN."""
    pts = [(c.lat, c.lon) for c in captures if c.lat is not None and c.lon is not None]
    n = len(captures)
    if not pts:
        return [NAN] * n, [NAN] * n, None
    lat0 = statistics.median(p[0] for p in pts)
    lon0 = statistics.median(p[1] for p in pts)
    epsg = utm_epsg(lat0, lon0)
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    xs = [NAN] * n
    ys = [NAN] * n
    for i, c in enumerate(captures):
        if c.lat is not None and c.lon is not None:
            x, y = tr.transform(c.lon, c.lat)
            xs[i], ys[i] = float(x), float(y)
    return xs, ys, epsg


def heading_deg(dx: float, dy: float) -> float:
    """북=0, 동=90 (시계방향)."""
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


def ang_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def circ_mean(angles: Sequence[float]) -> float:
    if not angles:
        return NAN
    sx = sum(math.sin(math.radians(a)) for a in angles)
    cy = sum(math.cos(math.radians(a)) for a in angles)
    return (math.degrees(math.atan2(sx, cy)) + 360.0) % 360.0


def off_nadir_deg(c: CaptureMeta) -> float:
    """연직(nadir)에서 벗어난 각. M3M 은 짐벌, RedEdge 는 DLS 자세. 정보 없으면 NaN."""
    if c.gimbal_pitch_deg is not None:
        # DJI 는 짐벌 요가 180° 뒤집힌 상태를 GimbalRollDegree=±180 으로 기록한다
        # (실측, 연직 촬영 그대로).
        # 롤은 광축 회전이므로 ±90° 범위로 접어서 기울기만 남긴다.
        roll = ((c.gimbal_roll_deg or 0.0) + 90.0) % 180.0 - 90.0
        return math.hypot(abs(c.gimbal_pitch_deg + 90.0), abs(roll))
    if c.pitch_deg is not None and c.roll_deg is not None:
        return math.hypot(c.pitch_deg, c.roll_deg)
    return NAN


def _percentile(values: Sequence[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return NAN
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


TARGET_SELF = "조사 라인 중앙값 · 목표 미지정"


def _resolve_target(
    configured: float | str, planned: float | None, observed: float | None
) -> tuple[float | None, str]:
    """목표값의 출처: 설정 숫자 → 임무 파일 → 데이터 중앙값(목표 미지정)."""
    if isinstance(configured, int | float):
        return float(configured), "설정"
    if planned is not None and planned > 0:
        return float(planned), "임무 파일"
    if observed is not None:
        return float(observed), TARGET_SELF
    return None, "없음"


def _oblique_runs(on_nadir: Sequence[float], cfg: FlightConfig) -> list[bool]:
    """``oblique_min_deg`` 이상 기울어진 캡처가 ``oblique_min_run`` 개 이상 이어진 구간."""
    n = len(on_nadir)
    big = [_ok(o) and o >= cfg.oblique_min_deg for o in on_nadir]
    out = [False] * n
    i = 0
    while i < n:
        if not big[i]:
            i += 1
            continue
        j = i
        while j < n and big[j]:
            j += 1
        if j - i >= cfg.oblique_min_run:
            for k in range(i, j):
                out[k] = True
        i = j
    return out


class TrajectoryResult:
    def __init__(self) -> None:
        self.group: CheckGroup = CheckGroup(key="flight", label="비행 궤적·고도")
        self.rows: list[CaptureRow] = []
        self.lines: list[LineInfo] = []
        self.takeoff_alt_msl_m: float | None = None
        self.agl_source: str = ""
        self.epsg: int | None = None
        self.track_length_m: float | None = None
        self.median_speed_mps: float | None = None
        self.mean_agl_m: float | None = None
        self.bbox: list[float] | None = None
        self.line_spacing_m: float | None = None
        self.agl_unknown_reason: str | None = None
        """이륙점 기준을 잡을 수 없어 AGL 을 N/A 로 둔 이유 (보고서 경고용)."""
        self.terrain_follow: bool = False
        """지형추종(DSM 추종) 비행으로 추정되어 이륙점 기준 고도로는 AGL 판정을 보류했는지."""
        self.terrain_follow_evidence: dict[str, float] = {}
        self.agl_is_true: bool = False
        """AGL 이 DEM 기준 실제 대지고도인지 (True) / 이륙점 기준 상대고도인지 (False)."""
        self.target_agl_m: float | None = None
        self.target_agl_source: str = ""
        """"설정" · "임무 파일" · "조사 라인 중앙값"(목표 미지정)."""
        self.target_speed_mps: float | None = None
        self.target_speed_source: str = ""
        self.oblique_indices: list[int] = []
        """의도된 경사 촬영(DJI 고도 최적화 등)으로 보아 판정에서 제외한 캡처."""


def _agl(
    captures: Sequence[CaptureMeta],
    cfg: FlightConfig,
    res: TrajectoryResult,
    dem: DemSampler | None = None,
) -> list[float]:
    if dem is not None:
        ground = dem.sample([c.lon for c in captures], [c.lat for c in captures])
        agl = [
            (c.alt_msl_m - cfg.geoid_offset_m - g) if c.alt_msl_m is not None and _ok(g) else NAN
            for c, g in zip(captures, ground, strict=True)
        ]
        n_ok = sum(1 for a in agl if _ok(a))
        if captures and n_ok >= max(3, len(captures) // 2):
            res.agl_source = (
                f"기체 고도 − DEM 지표고도 ({dem.describe()}, "
                f"지오이드 보정 {cfg.geoid_offset_m:+.1f} m)"
            )
            res.agl_is_true = True
            if n_ok < len(captures):
                n_miss = len(captures) - n_ok
                res.agl_unknown_reason = (
                    f"DEM 범위 밖·결측 캡처 {n_miss}개는 AGL 미계산 ({dem.describe()})"
                )
            return agl
        res.agl_unknown_reason = (
            f"DEM {dem.describe()} 이 캡처 위치를 덮지 않아 (유효 {n_ok}/{len(captures)}) "
            "이륙점 기준 상대고도로 대체"
        )
    if captures and all(c.alt_rel_m is not None for c in captures):
        res.agl_source = "기체 기록 상대고도 (RelativeAltitude, 이륙점 기준)"
        return [c.alt_rel_m if c.alt_rel_m is not None else NAN for c in captures]
    if isinstance(cfg.takeoff_alt_msl_m, int | float):
        takeoff = float(cfg.takeoff_alt_msl_m)
        res.agl_source = f"GPS 고도 − 설정 이륙점 고도 {takeoff:.1f} m"
    else:
        alts = [c.alt_msl_m for c in captures if c.alt_msl_m is not None]
        first = alts[0] if alts else None
        takeoff = NAN if first is None else first
        res.agl_source = "GPS 고도 − 첫 캡처 고도 (이륙점 가정, 수관 높이 미보정)"
        # 첫 캡처가 이미 비행고도라면(지상 패널 촬영 없음) 이륙점을 알 수 없다 → AGL N/A
        min_rise = (
            max(30.0, 0.25 * cfg.target_agl_m)
            if isinstance(cfg.target_agl_m, int | float)
            else 30.0
        )
        if alts and statistics.median(alts) - takeoff < min_rise:
            res.agl_unknown_reason = (
                "첫 캡처가 이미 비행고도라 이륙점 고도를 추정할 수 없음. "
                "설정 [flight].takeoff_alt_msl_m 에 이륙점 고도를 넣거나 DEM 을 지정하면 계산됨"
            )
            res.agl_source = "이륙점 고도 불명 (AGL 미계산)"
            res.takeoff_alt_msl_m = None
            return [NAN] * len(captures)
    res.takeoff_alt_msl_m = _opt(takeoff)
    return [(c.alt_msl_m - takeoff) if c.alt_msl_m is not None else NAN for c in captures]


def _segment_lines(
    xs: Sequence[float],
    head: Sequence[float],
    intervals: Sequence[float],
    cfg: FlightConfig,
) -> list[int]:
    """캡처별 라인 번호 (-1 = 좌표 없음)."""
    n = len(xs)
    valid_iv = _vals(intervals)
    med_iv = (
        statistics.median([v for v in valid_iv if v > 0]) if any(v > 0 for v in valid_iv) else NAN
    )
    line_of = [-1] * n
    line_id = -1
    ref = NAN
    members: list[float] = []
    for i in range(n):
        if not _ok(xs[i]):
            continue
        h = head[i]
        if i == 0 or not _ok(h):
            line_id += 1
            ref = NAN
            members = []
            line_of[i] = line_id
            continue
        big_gap = _ok(med_iv) and _ok(intervals[i]) and intervals[i] > med_iv * 3
        if not _ok(ref):
            ref = h
            members = [h]
            if line_of[i - 1] < 0:
                line_id += 1
            line_of[i] = line_id
            continue
        if ang_diff(h, ref) > cfg.line_heading_break_deg or big_gap:
            prev_line = line_of[i - 1]
            line_id += 1
            ref = NAN
            members = []
            line_of[i] = line_id
            # 구간 (i-1 → i) 가 새 방향이므로 i-1 도 새 라인의 시작점이다.
            # 단, i-1 이 이미 조사 라인(충분히 긴 라인)의 끝이면 그대로 둔다.
            n_prev = sum(1 for v in line_of[:i] if v == prev_line)
            if not big_gap and prev_line >= 0 and n_prev < cfg.min_captures_per_line:
                line_of[i - 1] = line_id
                ref = h
                members = [h]
            continue
        members.append(h)
        ref = circ_mean(members[-10:])
        line_of[i] = line_id
    return line_of


def _line_infos(
    line_of: Sequence[int],
    head: Sequence[float],
    agl: Sequence[float],
    speed: Sequence[float],
    seg_len: Sequence[float],
    cfg: FlightConfig,
) -> list[LineInfo]:
    lines: list[LineInfo] = []
    n_lines = max(line_of) + 1 if line_of else 0
    for lid in range(n_lines):
        idx = [i for i, ln in enumerate(line_of) if ln == lid]
        if not idx:
            continue
        hs = _vals([head[i] for i in idx[1:]])  # 첫 캡처의 진입 구간은 이전 라인/전환 방향
        ag = _vals([agl[i] for i in idx])
        sp = _vals([speed[i] for i in idx[1:]])
        length = sum(_vals([seg_len[i] for i in idx[1:]]))
        lines.append(
            LineInfo(
                index=lid,
                start_capture=idx[0],
                end_capture=idx[-1],
                n_captures=len(idx),
                heading_deg=round(circ_mean(hs), 1) if hs else NAN,
                length_m=round(length, 1),
                mean_agl_m=round(statistics.fmean(ag), 1) if ag else None,
                std_agl_m=round(statistics.pstdev(ag), 2) if len(ag) > 1 else None,
                mean_speed_mps=round(statistics.fmean(sp), 2) if sp else None,
                is_transit=len(idx) < cfg.min_captures_per_line,
            )
        )
    return lines


def _line_spacing(
    lines: Sequence[LineInfo], xs: Sequence[float], ys: Sequence[float]
) -> float | None:
    if len(lines) < 2:
        return None
    dom = circ_mean([ln.heading_deg % 180.0 for ln in lines if _ok(ln.heading_deg)])
    if not _ok(dom):
        return None
    nx, ny = math.cos(math.radians(dom)), -math.sin(math.radians(dom))  # 진행방향의 법선
    offs: list[float] = []
    for ln in lines:
        px = _vals(xs[ln.start_capture : ln.end_capture + 1])
        py = _vals(ys[ln.start_capture : ln.end_capture + 1])
        if px and py:
            offs.append(statistics.fmean(px) * nx + statistics.fmean(py) * ny)
    offs.sort()
    diffs = [b - a for a, b in pairwise(offs) if b - a > 1.0]
    return statistics.median(diffs) if diffs else None


def _terrain_follow(
    survey: Sequence[LineInfo],
    xs: Sequence[float],
    ys: Sequence[float],
    agl: Sequence[float],
    cfg: FlightConfig,
) -> tuple[bool, dict[str, float]]:
    """지형추종(DSM 추종) 비행 감지.

    두 신호를 함께 본다 (docs/QA_RULES.md):
    1. 라인 안 고도 변화폭(p95−p5) 의 중앙값 ≥ ``terrain_follow_min_range_m``
       (RTK 고도 유지 비행이라면 cm 급이라 수 m 를 넘지 않는다)
    2. 인접 조사 라인의 고도 프로파일(진행축 좌표에 대한 고도) 상관계수 중앙값
       ≥ ``terrain_follow_corr_min`` — 같은 지형을 왕복하면 같은 굴곡이 반복된다. 바람·고도 유지
       실패는 라인마다 무작위라 상관이 낮다.
    """
    lines = [ln for ln in survey if ln.n_captures >= 8]
    if len(lines) < 2:
        return False, {}
    dom = circ_mean([ln.heading_deg % 180.0 for ln in lines if _ok(ln.heading_deg)])
    if not _ok(dom):
        return False, {}
    ux, uy = math.sin(math.radians(dom)), math.cos(math.radians(dom))  # 진행축 단위벡터 (북=0)
    profiles: list[tuple[list[float], list[float]]] = []
    ranges: list[float] = []
    for ln in lines:
        pts = [
            (xs[i] * ux + ys[i] * uy, agl[i])
            for i in range(ln.start_capture, ln.end_capture + 1)
            if _ok(xs[i]) and _ok(agl[i])
        ]
        if len(pts) < 8:
            continue
        pts.sort()
        vals = [p[1] for p in pts]
        ranges.append(_percentile(vals, 95) - _percentile(vals, 5))
        profiles.append(([p[0] for p in pts], vals))
    if len(profiles) < 2:
        return False, {}
    corrs: list[float] = []
    for (sa, va), (sb, vb) in pairwise(profiles):
        lo, hi = max(sa[0], sb[0]), min(sa[-1], sb[-1])
        span = min(sa[-1] - sa[0], sb[-1] - sb[0])
        if span <= 0 or hi - lo < 0.5 * span:
            continue
        grid = [lo + (hi - lo) * k / 19 for k in range(20)]
        r = _pearson(_interp(sa, va, grid), _interp(sb, vb, grid))
        if _ok(r):
            corrs.append(r)
    if len(corrs) < 2:
        return False, {}
    all_agl = _vals([agl[i] for ln in lines for i in range(ln.start_capture, ln.end_capture + 1)])
    ev = {
        "range_m": round(statistics.median(ranges), 1),
        "corr": round(statistics.median(corrs), 2),
        "n_pairs": float(len(corrs)),
        "min_m": round(min(all_agl), 1),
        "max_m": round(max(all_agl), 1),
    }
    detected = (
        ev["range_m"] >= cfg.terrain_follow_min_range_m
        and ev["corr"] >= cfg.terrain_follow_corr_min
    )
    return detected, ev


def _interp(xs: Sequence[float], ys: Sequence[float], grid: Sequence[float]) -> list[float]:
    """단조 증가 xs 에 대한 선형 보간 (양 끝은 끝값 유지)."""
    out: list[float] = []
    j = 0
    for g in grid:
        while j < len(xs) - 2 and xs[j + 1] < g:
            j += 1
        x0, x1 = xs[j], xs[j + 1]
        t = 0.0 if x1 == x0 else (g - x0) / (x1 - x0)
        t = min(max(t, 0.0), 1.0)
        out.append(ys[j] + (ys[j + 1] - ys[j]) * t)
    return out


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n < 3:
        return NAN
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return NAN
    return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / (sa * sb)


def analyze_trajectory(
    captures: Sequence[CaptureMeta],
    sensor_kind: SensorKind,
    cfg: FlightConfig,
    rtk_cfg: RtkConfig,
    flight_index: int,
    intervals_in: Sequence[float | None],
    dem: DemSampler | None = None,
    plan: MissionPlan | None = None,
) -> TrajectoryResult:
    res = TrajectoryResult()
    n = len(captures)
    intervals = [NAN if v is None else v for v in intervals_in]
    xs, ys, epsg = project(captures)
    res.epsg = epsg
    agl = _agl(captures, cfg, res, dem)

    # --- 속도·방향
    speed = [NAN] * n
    head = [NAN] * n
    seg_len = [NAN] * n
    for i in range(1, n):
        if not (_ok(xs[i - 1]) and _ok(xs[i])):
            continue
        dx, dy = xs[i] - xs[i - 1], ys[i] - ys[i - 1]
        seg_len[i] = math.hypot(dx, dy)
        if seg_len[i] >= 0.5:  # 정지 상태(패널 촬영 등)는 방향 없음
            head[i] = heading_deg(dx, dy)
        if _ok(intervals[i]) and intervals[i] > 0:
            speed[i] = seg_len[i] / intervals[i]

    # --- 경사 촬영 구간 (DJI 고도 최적화: 임무 끝에 짐벌 45° 로 중심을 향해 촬영) — 판정 제외
    on_nadir = [off_nadir_deg(c) for c in captures]
    oblique = _oblique_runs(on_nadir, cfg)
    res.oblique_indices = [i for i, o in enumerate(oblique) if o]

    # --- 라인
    line_of = _segment_lines(xs, head, intervals, cfg)
    for i in res.oblique_indices:
        line_of[i] = -1
    lines = _line_infos(line_of, head, agl, speed, seg_len, cfg)
    res.lines = lines
    survey = [ln for ln in lines if not ln.is_transit]
    on_line = {i for ln in survey for i in range(ln.start_capture, ln.end_capture + 1)}
    res.line_spacing_m = _line_spacing(survey, xs, ys)

    # --- 지형추종 감지: 이륙점 기준 고도가 지형을 따라 오르내리면 AGL 판정을 보류한다
    if cfg.terrain_follow_detect and not res.agl_is_true:
        res.terrain_follow, res.terrain_follow_evidence = _terrain_follow(survey, xs, ys, agl, cfg)
        if res.terrain_follow:
            res.agl_source += " · 지형추종 추정 (실제 AGL 아님)"

    # --- 목표고도·속도: 설정값 → 임무 파일 → 데이터 자체(중앙값, 일관성만 판정)
    line_agl_pre = _vals([agl[i] for i in on_line])
    line_speed_pre = _vals(
        [speed[i] for ln in survey for i in range(ln.start_capture + 1, ln.end_capture + 1)]
    )
    plan_h = (plan.shoot_height_m or plan.height_m) if plan is not None else None
    res.target_agl_m, res.target_agl_source = _resolve_target(
        cfg.target_agl_m, plan_h, statistics.median(line_agl_pre) if line_agl_pre else None
    )
    res.target_speed_mps, res.target_speed_source = _resolve_target(
        cfg.target_speed_mps,
        plan.speed_mps if plan is not None else None,
        statistics.median(line_speed_pre) if line_speed_pre else None,
    )
    target_agl = res.target_agl_m if res.target_agl_m is not None else NAN

    # --- 캡처 행
    tol = target_agl * cfg.agl_tolerance_warn_pct / 100.0 if _ok(target_agl) else NAN
    rows: list[CaptureRow] = []
    for i, c in enumerate(captures):
        flags: list[str] = []
        if c.missing_bands:
            flags.append("missing_band")
        if not res.terrain_follow and _ok(agl[i]) and _ok(tol) and abs(agl[i] - target_agl) > tol:
            flags.append("agl_out")
        if oblique[i]:
            flags.append("oblique")
        elif _ok(on_nadir[i]) and on_nadir[i] > cfg.nadir_warn_deg:
            flags.append("off_nadir")
        rtk_fixed: bool | None = None
        if c.rtk is not None and c.rtk.flag is not None:
            rtk_fixed = c.rtk.flag in rtk_cfg.fixed_flags
            if not rtk_fixed:
                flags.append("rtk_not_fixed")
        rows.append(
            CaptureRow(
                index=i,
                capture_id=c.capture_id,
                flight_index=flight_index,
                time_utc=c.time_utc,
                lat=c.lat,
                lon=c.lon,
                x_m=_opt(xs[i], 2),
                y_m=_opt(ys[i], 2),
                alt_msl_m=c.alt_msl_m,
                agl_m=_opt(agl[i], 2),
                speed_mps=_opt(speed[i], 2),
                heading_deg=_opt(head[i], 1),
                interval_s=_opt(intervals[i], 3),
                line=line_of[i] if line_of[i] >= 0 else None,
                off_nadir_deg=_opt(on_nadir[i], 2),
                n_bands=len(c.bands),
                missing_bands=",".join(c.missing_bands),
                rtk_fixed=rtk_fixed,
                irradiance=c.mean_irradiance(),
                solar_elevation_deg=c.solar_elevation_deg,
                flags=",".join(flags),
            )
        )
    res.rows = rows

    # --- 요약값
    seg_valid = _vals(seg_len)
    res.track_length_m = round(sum(seg_valid), 1) if seg_valid else None
    line_speeds = _vals([speed[i] for i in on_line]) or _vals(speed)
    res.median_speed_mps = statistics.median(line_speeds) if line_speeds else None
    line_agl = _vals([agl[i] for i in on_line]) or _vals(agl)
    res.mean_agl_m = statistics.fmean(line_agl) if line_agl else None
    lats = [c.lat for c in captures if c.lat is not None]
    lons = [c.lon for c in captures if c.lon is not None]
    if lats and lons:
        res.bbox = [min(lons), min(lats), max(lons), max(lats)]

    # --- 판정 항목
    m: list[Metric] = []
    tf = res.terrain_follow
    ev = res.terrain_follow_evidence
    tf_note = (
        f"지형추종 비행으로 추정 (라인 안 고도 변화 {fmt(ev.get('range_m'), 0, 'm')}, "
        f"인접 라인 프로파일 상관 r={fmt(ev.get('corr'), 2)}). "
        f"이륙점 기준 고도 {fmt(ev.get('min_m'), 0)}–{fmt(ev.get('max_m'), 0, 'm')} 는 실제 AGL 이 "
        "아니므로 판정 보류"
        if tf
        else ""
    )
    if tf:
        tf_reason = tf_note
    elif ev:
        tf_reason = (
            f"라인 안 고도 변화 중앙값 {fmt(ev.get('range_m'), 1, 'm')}, "
            f"인접 라인 상관 {fmt(ev.get('corr'), 2)} "
            f"(기준: 변화 ≥ {cfg.terrain_follow_min_range_m:.0f} m 이고 "
            f"r ≥ {cfg.terrain_follow_corr_min:.1f})"
        )
    elif res.agl_is_true:
        tf_reason = "DEM 기준 실제 AGL 이라 해당 없음"
    else:
        tf_reason = "감지 불가 (조사 라인 2개 미만 또는 고도 없음)"
    tf_metric = info_metric(
        "flight.terrain_follow",
        "지형추종(DSM 추종) 비행 추정",
        "예" if tf else "아니오",
        None,
        tf_reason,
    )
    if tf:
        tf_metric.next_action = (
            "DSM 추종 설정으로 비행했는지 확인. 실제 AGL 판정이 필요하면 설정 [flight].dem_path 에 "
            "조사지 DEM/DSM(GeoTIFF) 을 지정하고 재분석"
        )
    m.append(tf_metric)
    self_ref = res.target_agl_source == TARGET_SELF
    st, dev = grade_deviation_pct(
        res.mean_agl_m, target_agl, cfg.agl_tolerance_warn_pct, cfg.agl_tolerance_fail_pct
    )
    if self_ref and res.mean_agl_m is not None:
        st = Status.PASS  # 목표가 없으면 평균은 정보값. 일관성은 아래 허용범위 항목이 본다
    if tf:
        st = Status.NA
    if tf:
        agl_reason = tf_note + f"; 기준: {res.agl_source}"
    elif res.mean_agl_m is not None and self_ref:
        agl_reason = (
            f"조사 라인 캡처 {len(line_agl)}개 평균; 목표고도 미지정이라 중앙값 "
            f"{fmt(res.target_agl_m, 0, 'm')} 를 기준으로 일관성만 판정 "
            f"(목표는 설정 [flight].target_agl_m 또는 임무 KMZ 로 지정); 기준: {res.agl_source}"
        )
    elif res.mean_agl_m is not None:
        agl_reason = (
            f"조사 라인 캡처 {len(line_agl)}개 평균, 목표 {fmt(res.target_agl_m, 0, 'm')}"
            f"({res.target_agl_source}) 대비 편차 {fmt(dev, 1, '%')}; 기준: {res.agl_source}"
        )
    else:
        agl_reason = res.agl_unknown_reason or "고도 정보 없음"
    m.append(
        Metric(
            key="flight.agl_mean",
            label="평균 비행고도 (AGL)",
            value=round(res.mean_agl_m, 1) if res.mean_agl_m is not None else None,
            unit="m",
            threshold=(
                f"목표 {fmt(res.target_agl_m, 0, 'm')} ({res.target_agl_source}) "
                f"± {cfg.agl_tolerance_warn_pct:.0f}% (WARN) / "
                f"{cfg.agl_tolerance_fail_pct:.0f}% (FAIL)"
                if not self_ref
                else "목표 미지정 — 정보 항목 (일관성은 허용범위 항목)"
            ),
            status=st,
            reason=agl_reason,
            next_action=(
                "후처리 해상도·중복도 재계산 · 고도가 목표보다 낮으면 중복 부족 구간 확인 · "
                "이륙점 고도 기준 점검\n영향: 실제 GSD 가 계획과 다름"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            details={
                "agl_source": res.agl_source,
                "takeoff_alt_msl_m": res.takeoff_alt_msl_m,
                "terrain_follow": tf,
                "target_agl_m": res.target_agl_m,
                "target_source": res.target_agl_source,
            },
        )
    )
    within = [a for a in line_agl if _ok(tol) and abs(a - target_agl) <= tol]
    pct_within = (len(within) / len(line_agl) * 100.0) if line_agl and _ok(tol) else None
    st = grade_min(pct_within, cfg.agl_within_tolerance_min_pct, cfg.agl_within_tolerance_fail_pct)
    if tf:
        st = Status.NA
        within_reason = tf_note
    elif line_agl:
        within_reason = (
            f"{len(within)}/{len(line_agl)} 캡처가 "
            f"{target_agl - tol:.0f}–{target_agl + tol:.0f} m 범위"
            + (f" (기준 {fmt(res.target_agl_m, 0, 'm')}: {res.target_agl_source})")
        )
    else:
        within_reason = res.agl_unknown_reason or "고도 정보 없음"
    m.append(
        Metric(
            key="flight.agl_within_tolerance",
            label="고도 허용범위 이내 캡처 비율",
            value=round(pct_within, 1) if pct_within is not None and not tf else None,
            unit="%",
            threshold=(
                f"±{cfg.agl_tolerance_warn_pct:.0f}% 이내 ; "
                f"WARN < {cfg.agl_within_tolerance_min_pct:.0f}%, "
                f"FAIL < {cfg.agl_within_tolerance_fail_pct:.0f}%"
            ),
            status=st,
            reason=within_reason,
            next_action=(
                "범위 밖 캡처가 몰린 라인(라인 표 참고)을 재비행 후보로 검토"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            gate=True,
        )
    )
    stds = [ln.std_agl_m for ln in survey if ln.std_agl_m is not None]
    if stds:
        std_med: float | None = statistics.median(stds)
    elif len(line_agl) > 1:
        std_med = statistics.pstdev(line_agl)
    else:
        std_med = None
    st = grade_max(std_med, cfg.altitude_std_warn_m, cfg.altitude_std_fail_m)
    if tf:
        st = Status.NA
    if stds:
        std_reason = f"조사 라인 {len(survey)}개, 최대 {fmt(max(stds), 2, 'm')}" + (
            "; 지형추종 추정 — 지형 기복이 포함된 값이라 판정 보류" if tf else ""
        )
    else:
        std_reason = "라인 통계 없음"
    m.append(
        Metric(
            key="flight.altitude_std",
            label="라인별 고도 표준편차 (중앙값)",
            value=round(std_med, 2) if std_med is not None else None,
            unit="m",
            threshold=f"WARN ≥ {cfg.altitude_std_warn_m} m, FAIL ≥ {cfg.altitude_std_fail_m} m",
            status=st,
            reason=std_reason,
            next_action=(
                "해당 라인의 GSD 편차를 후처리에서 확인\n원인: 지형추종 설정 또는 바람 영향 가능성"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    speed_self = res.target_speed_source == TARGET_SELF
    st, dev = grade_deviation_pct(
        res.median_speed_mps,
        res.target_speed_mps if res.target_speed_mps is not None else NAN,
        cfg.speed_tolerance_warn_pct,
        cfg.speed_tolerance_fail_pct,
    )
    if speed_self and res.median_speed_mps is not None:
        st = Status.PASS
    m.append(
        Metric(
            key="flight.speed",
            label="비행속도 (조사 라인 중앙값)",
            value=round(res.median_speed_mps, 2) if res.median_speed_mps is not None else None,
            unit="m/s",
            threshold=(
                f"목표 {fmt(res.target_speed_mps, 1, 'm/s')} ({res.target_speed_source}) "
                f"± {cfg.speed_tolerance_warn_pct:.0f}% / {cfg.speed_tolerance_fail_pct:.0f}%"
                if not speed_self
                else "목표 미지정 — 정보 항목"
            ),
            status=st,
            reason=(
                "연속 캡처 간 거리/시간으로 추정"
                + (
                    f", 목표 대비 편차 {fmt(dev, 1, '%')}"
                    if not speed_self
                    else " (목표속도는 설정 [flight].target_speed_mps 또는 임무 KMZ 로 지정)"
                )
                if res.median_speed_mps is not None
                else "속도 추정 불가"
            ),
            next_action=(
                "중복도 항목과 포화·노출 항목 함께 확인\n영향: 과속 시 종중복 부족·모션 블러 위험"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            details={
                "target_speed_mps": res.target_speed_mps,
                "target_source": res.target_speed_source,
            },
        )
    )
    ons = _vals([o for i, o in enumerate(on_nadir) if not oblique[i]])
    pct_off = (sum(1 for o in ons if o > cfg.nadir_warn_deg) / len(ons) * 100.0) if ons else None
    p95 = _percentile(ons, 95) if ons else NAN
    st = Status.NA
    if pct_off is not None:
        st = Status.PASS
        if pct_off > cfg.off_nadir_max_pct:
            st = Status.WARN
        if _ok(p95) and p95 > cfg.nadir_fail_deg:
            st = Status.FAIL
    attitude_src = "(짐벌 각)" if sensor_kind is SensorKind.DJI_M3M else "(DLS 자세, 고정 마운트)"
    m.append(
        Metric(
            key="flight.off_nadir",
            label="연직 이탈 캡처 비율",
            value=round(pct_off, 1) if pct_off is not None else None,
            unit="%",
            threshold=(
                f"이탈 > {cfg.nadir_warn_deg}° ; WARN 비율 > {cfg.off_nadir_max_pct:.0f}%, "
                f"FAIL p95 > {cfg.nadir_fail_deg}°"
            ),
            status=st,
            reason=(
                f"p95 {fmt(_opt(p95), 1, '°')}, 최대 {fmt(max(ons), 1, '°')} {attitude_src}"
                + (f"; 경사 촬영 {len(res.oblique_indices)}장 제외" if res.oblique_indices else "")
                if ons
                else "자세 정보 없음"
            ),
            next_action=(
                "바람이 강했던 구간이면 재비행 검토\n"
                "영향: 기울어진 캡처는 정사영상 왜곡·중복 불균일"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    if res.oblique_indices:
        ob = res.oblique_indices
        ob_angles = _vals([on_nadir[i] for i in ob])
        m.append(
            info_metric(
                "flight.oblique",
                "경사 촬영 캡처 (고도 최적화 등)",
                len(ob),
                "장",
                f"캡처 {ob[0] + 1}–{ob[-1] + 1}번, "
                f"기울기 {fmt(statistics.median(ob_angles), 0, '°')} "
                "— DJI 고도 최적화처럼 임무 절차에 포함된 경사 촬영으로 보아 연직 이탈·조사 라인·"
                "중복도 판정에서 제외 (후처리에는 그대로 투입)",
            )
        )
    m.append(
        info_metric(
            "flight.lines",
            "조사 라인 수",
            len(survey),
            "개",
            f"전환 구간 {len(lines) - len(survey)}개 제외; "
            + (f"경사 촬영 {len(res.oblique_indices)}장 제외; " if res.oblique_indices else "")
            + f"라인 간격 추정 {fmt(res.line_spacing_m, 1, 'm')}",
        )
    )
    m.append(
        info_metric(
            "flight.track_length",
            "비행 경로 길이",
            res.track_length_m,
            "m",
            f"UTM EPSG:{epsg} 투영" if epsg else "좌표 없음",
        )
    )

    # --- RTK (M3M) / GPS 정확도 (RedEdge)
    if sensor_kind is SensorKind.DJI_M3M:
        rtk_flags = [c.rtk.flag for c in captures if c.rtk is not None and c.rtk.flag is not None]
        n_fix = sum(1 for f in rtk_flags if f in rtk_cfg.fixed_flags)
        pct_fix = (n_fix / len(rtk_flags) * 100.0) if rtk_flags else None
        st = grade_min(pct_fix, rtk_cfg.fixed_min_pct_warn, rtk_cfg.fixed_min_pct_fail)
        hgt = [
            c.rtk.std_hgt_m for c in captures if c.rtk is not None and c.rtk.std_hgt_m is not None
        ]
        flag_counts: dict[str, int] = {}
        for f in rtk_flags:
            flag_counts[str(f)] = flag_counts.get(str(f), 0) + 1
        hgt_med = statistics.median(hgt) if hgt else None
        m.append(
            Metric(
                key="rtk.fixed_pct",
                label="RTK 고정해(Fix) 캡처 비율",
                value=round(pct_fix, 1) if pct_fix is not None else None,
                unit="%",
                threshold=(
                    f"Fix = RtkFlag {rtk_cfg.fixed_flags} ; "
                    f"WARN < {rtk_cfg.fixed_min_pct_warn:.0f}%, "
                    f"FAIL < {rtk_cfg.fixed_min_pct_fail:.0f}%"
                ),
                status=st,
                reason=(
                    f"RtkFlag 분포 {flag_counts}; 고도 표준편차 중앙값 {fmt(hgt_med, 3, 'm')}"
                    if rtk_flags
                    else "RTK 태그 없음"
                ),
                next_action=(
                    "GCP 없이 절대정확도가 필요하면 재비행 · "
                    "아니면 후처리에서 카메라 위치 정확도 하향 설정\n"
                    "영향: 고정해가 아닌 캡처는 위치 정확도 m 급"
                    if st in (Status.WARN, Status.FAIL)
                    else None
                ),
                details={"flag_counts": flag_counts},
                gate=True,
            )
        )
    else:
        xy = [c.gps_xy_accuracy_m for c in captures if c.gps_xy_accuracy_m is not None]
        xy_med = statistics.median(xy) if xy else None
        m.append(
            Metric(
                key="rtk.fixed_pct",
                label="RTK 고정해 여부",
                status=Status.NA,
                reason=(
                    "Quick Mount 구성: 기체 RTK 좌표가 영상에 주입되지 않음. "
                    f"카메라 GPS 정확도 XY 중앙값 {fmt(xy_med, 2, 'm')} (수 m 급, 정상)"
                ),
                next_action=None,
            )
        )
    res.group = CheckGroup(key="flight", label="비행 궤적·고도", metrics=m)
    return res
