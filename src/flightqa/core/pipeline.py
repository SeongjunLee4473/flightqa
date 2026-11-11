"""파이프라인 오케스트레이션 (CLAUDE.md §3.3).

``discover → extract_metadata → analyze → judge`` 를 순서대로 부른다.
analyze = 무결성·궤적 → 픽셀 통계 → 노출·조도·패널·중복. GUI/CLI 는 ``run`` 하나만 호출한다.
진행률은 콜백, 취소는 ``CancelToken`` 으로 모든 루프에서 확인한다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shp_transform

from flightqa import __version__
from flightqa.config.schema import Config
from flightqa.core.analysis.dem import DemError, DemSampler
from flightqa.core.analysis.exposure import analyze_exposure
from flightqa.core.analysis.integrity import analyze_files, analyze_integrity
from flightqa.core.analysis.irradiance import analyze_irradiance
from flightqa.core.analysis.overlap import analyze_overlap
from flightqa.core.analysis.panel import analyze_panel, ground_flags
from flightqa.core.analysis.pixels import PixelTask, pixel_cache_key, run_pixel_tasks
from flightqa.core.analysis.survey_area import estimate_survey_area
from flightqa.core.analysis.trajectory import TrajectoryResult, analyze_trajectory
from flightqa.core.cache import MetadataCache
from flightqa.core.discovery import (
    build_captures,
    discover,
    regroup,
    sensor_by_kind,
    split_flights,
)
from flightqa.core.metadata import (
    CancelToken,
    ExifToolPool,
    FileEntry,
    exiftool_version_of,
)
from flightqa.core.mission import (
    MissionError,
    find_mission_files,
    load_mission,
    match_mission,
)
from flightqa.core.models import (
    CaptureMeta,
    CheckGroup,
    DiscoveryResult,
    ExcludedFile,
    Flight,
    FlightReport,
    FlightSummary,
    MissionPlan,
    RunReport,
    SensorKind,
    Status,
)
from flightqa.core.rules import flight_status, judge_run
from flightqa.core.sensors import all_sensors
from flightqa.core.sensors.base import Raw, SensorPlugin

log = logging.getLogger(__name__)

STAGES = ("discover", "extract", "analyze", "pixels", "judge")
STAGE_LABELS = {
    "discover": "발견",
    "extract": "메타데이터 추출",
    "analyze": "궤적·무결성 분석",
    "pixels": "포화·픽셀 분석",
    "judge": "판정",
    "render": "보고서",
    "export": "내보내기",
}


@dataclass(frozen=True, slots=True)
class Progress:
    stage: str
    current: int
    total: int
    message: str = ""

    @property
    def fraction(self) -> float:
        return self.current / self.total if self.total else 0.0


ProgressCb = Callable[[Progress], None]
DiscoveryCb = Callable[[DiscoveryResult], None]


def _emit(cb: ProgressCb | None, stage: str, cur: int, total: int, msg: str = "") -> None:
    if cb is not None:
        cb(Progress(stage, cur, total, msg))


@dataclass
class _FlightWork:
    flight: Flight
    intervals: list[float | None]
    integrity: CheckGroup
    traj: TrajectoryResult
    is_ground: list[bool]
    plan: MissionPlan | None = None
    """비행에 짝지어진 임무 파일 (목표고도·속도와 조사 구역 폴리곤의 출처)."""


PPK_PATTERNS = ("*_Timestamp.MRK", "*PPKOBS.obs", "*PPKRAW.bin", "*PPKNAV.nav")


MAX_MISSION_FILES = 20


def _load_missions(root: Path, config: Config, warnings: list[str]) -> list[MissionPlan]:
    """설정 경로(하나) 또는 폴더 자동 탐색(여러 개) 으로 임무 파일을 읽는다.

    어느 파일이 어느 비행 것인지는 비행마다 ``match_mission`` 이 폴리곤 겹침으로 고른다.
    읽기 실패는 경고로 남기고 그 파일만 뺀다.
    """
    sa = config.survey_area
    if not sa.enabled:
        return []
    if sa.mission_path:
        candidates = [Path(sa.mission_path)]
    elif sa.auto_mission_files:
        candidates = find_mission_files(root)
        if len(candidates) > MAX_MISSION_FILES:
            warnings.append(
                f"임무 파일이 {len(candidates)}개라 처음 {MAX_MISSION_FILES}개만 읽음 "
                "(survey_area.mission_path 로 지정 가능)"
            )
            candidates = candidates[:MAX_MISSION_FILES]
    else:
        return []
    plans: list[MissionPlan] = []
    for c in candidates:
        try:
            plans.append(load_mission(c))
        except MissionError as e:
            warnings.append(f"임무 파일을 읽지 못해 궤적 추정으로 대체: {e}")
    for p in plans:
        log.info("임무 파일 후보: %s (%s, 웨이포인트 %d)", p.source, p.template_type, p.n_waypoints)
    return plans


def _ppk_files(root: Path) -> list[str]:
    """DJI RTK 기체가 미션 폴더에 남기는 PPK 원시파일 (MRK 타임스탬프 + RINEX 관측).

    있으면 RTK Float 구간을 DJI Terra PPK 후처리로 복구할 수 있으므로 판정 문구에 알려준다.
    """
    found: list[str] = []
    for pat in PPK_PATTERNS:
        found.extend(p.name for p in root.glob(pat))
    return sorted(found)


def _add_ppk_hint(group: CheckGroup, ppk_files: list[str]) -> None:
    for m in group.metrics:
        if m.key == "rtk.fixed_pct" and m.status in (Status.WARN, Status.FAIL):
            kinds = ", ".join(sorted({n.rsplit("_", 1)[-1] if "_" in n else n for n in ppk_files}))
            base = (m.next_action or "").rstrip()
            if base and not base.endswith((".", "!", "?")):
                base += "."
            m.next_action = (base + " " if base else "") + (
                f"폴더에 PPK 원시파일({kinds})이 있으므로 재비행 대신 DJI Terra 의 PPK 후처리로 "
                "미고정 구간의 위치를 복구할 수 있음"
            )
            m.details["ppk_files"] = ppk_files


def run(
    root: Path,
    config: Config,
    *,
    on_progress: ProgressCb | None = None,
    on_discovery: DiscoveryCb | None = None,
    cancel: CancelToken | None = None,
    use_cache: bool | None = None,
    exiftool_exe: Path | None = None,
    skip_pixels: bool = False,
) -> RunReport:
    """폴더 하나를 끝까지 분석한다. 보고서 렌더·내보내기는 호출자가 한다."""
    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    warnings: list[str] = []
    cancel = cancel or CancelToken()

    # ---- 1. 발견
    t0 = time.perf_counter()
    cameras = {SensorKind(k): v.camera() for k, v in config.sensors.items()}
    sensors = all_sensors(cameras)
    discovery, entries = discover(
        root,
        sensors,
        follow_symlinks=config.discovery.follow_symlinks,
        cancel=cancel,
        on_progress=lambda n, where: _emit(on_progress, "discover", n, 0, where),
    )
    timings["discover"] = time.perf_counter() - t0
    if on_discovery is not None:
        on_discovery(discovery)
    exiftool_version = exiftool_version_of(exiftool_exe)

    if discovery.sensor is None:
        return _empty_report(
            discovery, config, exiftool_version, "지원 센서 데이터 없음", timings, warnings
        )
    sensor = sensor_by_kind(sensors, discovery.sensor)
    if discovery.confidence < 0.5:
        warnings.append(
            f"센서 감지 확신도가 낮음 ({discovery.confidence:.0%}): 파일명 규칙 불일치 파일이 많음"
        )

    # ---- 2. 추출 (캐시 → exiftool)
    t0 = time.perf_counter()
    cancel.check()
    use_cache = config.cache.enabled if use_cache is None else use_cache
    cache: MetadataCache | None = None
    if use_cache:
        try:
            cache = MetadataCache(config.cache.resolve_path(), exiftool_version)
        except Exception as e:  # noqa: BLE001 - 캐시 실패는 성능 문제일 뿐, 분석은 계속
            warnings.append(f"캐시를 열 수 없어 비활성화: {e}")
            cache = None
    records = _extract(entries, cache, config, exiftool_exe, on_progress, cancel)
    if cache:
        timings["cache_hits"] = float(cache.hits)
    timings["extract"] = time.perf_counter() - t0
    cancel.check()

    # ---- 3. 캡처 묶기·정규화·비행 분리
    t0 = time.perf_counter()
    grouped = regroup(sensor, discovery.root, records, set(discovery.unresolved))
    captures = build_captures(sensor, grouped)
    _apply_utc_offset(captures)
    flights = split_flights(captures, config.discovery.flight_gap_min)
    _emit(
        on_progress,
        "analyze",
        0,
        max(len(flights), 1),
        f"{len(captures)} 캡처, {len(flights)} 비행",
    )
    run_groups = [
        analyze_files(
            discovery, grouped.excluded, grouped.n_duplicates, grouped.n_reclaimed, config.integrity
        )
    ]

    # ---- 4a. 무결성·궤적
    dem: DemSampler | None = None
    if config.flight.dem_path:
        try:
            dem = DemSampler(Path(config.flight.dem_path))
        except DemError as e:
            warnings.append(f"DEM 을 쓰지 못해 이륙점 기준 상대고도로 분석: {e}")
    ppk_files = _ppk_files(discovery.root)
    missions = _load_missions(discovery.root, config, warnings)
    used_missions: list[MissionPlan] = []
    works: list[_FlightWork] = []
    for i, fl in enumerate(flights):
        cancel.check()
        caps = fl.captures
        offset = next((c.utc_offset_min for c in caps if c.utc_offset_min is not None), None)
        integ, intervals, _gaps = analyze_integrity(caps, sensor, config.integrity, offset)
        # 임무 파일은 궤적 분석 앞에서 짝짓는다 — 목표고도·속도의 출처가 되기 때문
        plan: MissionPlan | None = None
        plan_frac = 0.0
        if missions:
            pts = [(c.lon, c.lat) for c in caps if c.lon is not None and c.lat is not None]
            plan, plan_frac = match_mission(
                missions,
                pts,
                discovery.root,
                fl.start_utc,
                config.survey_area.mission_match_min_pct,
            )
            if plan is None:
                warnings.append(
                    f"{fl.flight_id}: 임무 파일 {len(missions)}개 중 비행 캡처와 겹치는 폴리곤이 "
                    f"없어(최대 {plan_frac:.0f}%) 궤적 추정을 씀"
                )
            else:
                log.info(
                    "%s: 임무 파일 %s (캡처 %.0f%% 폴리곤 안)", fl.flight_id, plan.source, plan_frac
                )
        traj = analyze_trajectory(
            caps, sensor.kind, config.flight, config.rtk, fl.index, intervals, dem, plan
        )
        if traj.agl_unknown_reason:
            warnings.append(f"{fl.flight_id}: {traj.agl_unknown_reason}")
        if ppk_files:
            _add_ppk_hint(traj.group, ppk_files)
        if len(caps) < config.discovery.min_captures_per_flight:
            warnings.append(
                f"{fl.flight_id}: 캡처 {len(caps)}개는 비행으로 보기에 적음 "
                f"(기준 {config.discovery.min_captures_per_flight}) — 단발 촬영 또는 패널 촬영 가능"
            )
        agl = [r.agl_m for r in traj.rows]
        works.append(
            _FlightWork(fl, intervals, integ, traj, ground_flags(caps, agl, config.panel), plan)
        )
        _emit(on_progress, "analyze", i + 1, len(flights), fl.flight_id)
    timings["analyze"] = time.perf_counter() - t0

    # ---- 4b. 픽셀 통계 (포화·패널 힌트)
    t0 = time.perf_counter()
    stats: dict[tuple[int, int, str], dict[str, Any]] = {}
    if not skip_pixels:
        stats = _pixel_stats(works, records, sensor, config, cache, on_progress, cancel)
    timings["pixels"] = time.perf_counter() - t0
    if cache:
        cache.close()

    # ---- 4c. 노출·조도·패널·중복 → 비행 보고서
    t0 = time.perf_counter()
    reports: list[FlightReport] = []
    camera = sensor.camera
    for w in works:
        cancel.check()
        fl, traj, caps = w.flight, w.traj, w.flight.captures
        fstats = {(i, b): v for (fi, i, b), v in stats.items() if fi == fl.index}
        exp = analyze_exposure(caps, sensor, config.exposure, fstats, w.is_ground)
        survey_lines = {ln.index for ln in traj.lines if not ln.is_transit}
        survey_idx = [
            r.index for r in traj.rows if r.line in survey_lines and not w.is_ground[r.index]
        ] or [r.index for r in traj.rows if not w.is_ground[r.index]]
        irr = analyze_irradiance(
            caps, config.irradiance, survey_idx, sensor.kind is SensorKind.REDEDGE_MX
        )
        panel_group, panel_info = analyze_panel(
            caps,
            w.is_ground,
            traj.lines,
            config.panel,
            config.exposure,
            fstats,
            sensor.spectral_band_keys,
            sensor.kind,
        )
        ov = analyze_overlap(
            traj.rows, traj.lines, camera, config.overlap, traj.epsg, traj.line_spacing_m
        )
        mission_geom = None
        mission_src: str | None = None
        plan = w.plan
        if plan is not None and traj.epsg is not None:
            if all(u.source != plan.source for u in used_missions):
                used_missions.append(plan)
            tr = Transformer.from_crs(4326, traj.epsg, always_xy=True)
            mission_geom = shp_transform(tr.transform, shape(plan.polygon_geojson))
            mission_src = plan.source
        sa = estimate_survey_area(
            traj.rows,
            traj.lines,
            w.is_ground,
            camera,
            ov.union,
            config.survey_area,
            traj.epsg,
            mission=mission_geom,
            mission_source=mission_src,
        )
        ov.group.metrics.extend(sa.metrics)
        for i, r in enumerate(traj.rows):
            r.is_ground = w.is_ground[i]
            first_band = next(
                (caps[i].bands[b] for b in sensor.spectral_band_keys if b in caps[i].bands), None
            )
            r.primary_file = str(first_band.path) if first_band else None
            r.sat_max_pct = exp.sat_max_pct[i]
            r.underexposed = exp.underexposed[i]
            r.forward_overlap_pct = ov.forward_pct[i]
            r.gsd_cm = ov.gsd_cm[i]
            el = irr.solar_elevation_calc[i]
            r.solar_elevation_calc_deg = round(el, 2) if el is not None else None
            flags = [f for f in r.flags.split(",") if f]
            if r.is_ground:
                flags.append("ground")
            if i in exp.saturated_indices:
                flags.append("saturated")
            if i in irr.event_indices:
                flags.append("irradiance_drop")
            r.flags = ",".join(flags)
        groups: list[CheckGroup] = [
            w.integrity,
            traj.group,
            ov.group,
            exp.group,
            irr.group,
            panel_group,
        ]
        reports.append(
            FlightReport(
                summary=_summary(w),
                status=flight_status(groups, config.judgement.warn_to_fail_count),
                groups=groups,
                lines=traj.lines,
                rows=traj.rows,
                takeoff_alt_msl_m=traj.takeoff_alt_msl_m,
                agl_source=traj.agl_source,
                terrain_follow=traj.terrain_follow,
                pixel_rows=exp.pixel_rows,
                band_hist=exp.band_hist,
                hist_edges=exp.hist_edges,
                coverage=ov.coverage,
                survey_area=sa.area,
                panel=panel_info,
            )
        )
    timings["analyze"] += time.perf_counter() - t0

    # ---- 5. 판정
    t0 = time.perf_counter()
    _emit(on_progress, "judge", 0, 1)
    verdict = judge_run(reports, run_groups)
    status, headline, detail = verdict.status, verdict.headline, verdict.detail
    if not captures:
        status, headline, detail = Status.NA, "판정 불가", "유효한 캡처가 없음"
    timings["judge"] = time.perf_counter() - t0
    timings["total"] = time.perf_counter() - t_all
    _emit(on_progress, "judge", 1, 1, f"{headline} · {detail}")

    return RunReport(
        generated_at=datetime.now(UTC),
        flightqa_version=__version__,
        exiftool_version=exiftool_version,
        root=discovery.root,
        sensor=sensor.kind,
        sensor_display=sensor.display_name,
        discovery=discovery,
        n_captures=len(captures),
        excluded=grouped.excluded,
        run_groups=run_groups,
        flights=reports,
        status=status,
        headline=headline,
        headline_detail=detail,
        next_actions=verdict.actions,
        action_items=verdict.items,
        config=config.model_dump(mode="json"),
        sample_tags=_sample_tags(records),
        mission=used_missions[0] if used_missions else None,
        missions=used_missions,
        timings_s={k: round(v, 3) for k, v in timings.items()},
        warnings=warnings,
    )


def inspect(root: Path, config: Config, *, cancel: CancelToken | None = None) -> DiscoveryResult:
    """발견 단계만 (``flightqa inspect``)."""
    cameras = {SensorKind(k): v.camera() for k, v in config.sensors.items()}
    result, _ = discover(
        root, all_sensors(cameras), follow_symlinks=config.discovery.follow_symlinks, cancel=cancel
    )
    return result


# --------------------------------------------------------------------------- 내부


def _extract(
    entries: list[FileEntry],
    cache: MetadataCache | None,
    config: Config,
    exiftool_exe: Path | None,
    on_progress: ProgressCb | None,
    cancel: CancelToken,
) -> list[tuple[FileEntry, Raw]]:
    records: list[tuple[FileEntry, Raw]] = []
    cached: dict[str, Raw] = cache.get_many(entries) if cache else {}
    todo: list[FileEntry] = []
    for entry in entries:
        raw = cached.get(entry.key)
        if raw is not None:
            records.append((entry, raw))
        else:
            todo.append(entry)
    _emit(on_progress, "extract", len(records), len(entries), f"캐시 적중 {len(records)}")
    if not todo:
        return records
    with ExifToolPool(
        exiftool_exe,
        workers=config.extract.resolve_workers(),
        chunk_size=config.extract.chunk_size,
    ) as pool:
        pending: list[tuple[FileEntry, Raw]] = []
        base = len(records)

        def _prog(done: int, _total: int, name: str) -> None:
            _emit(on_progress, "extract", base + done, len(entries), name)

        for entry, raw in pool.extract(todo, on_progress=_prog, cancel=cancel):
            records.append((entry, raw))
            pending.append((entry, raw))
            if cache and len(pending) >= 500:
                cache.put_many(pending)
                pending = []
        if cache and pending:
            cache.put_many(pending)
    return records


def _pixel_stats(
    works: list[_FlightWork],
    records: list[tuple[FileEntry, Raw]],
    sensor: SensorPlugin,
    config: Config,
    cache: MetadataCache | None,
    on_progress: ProgressCb | None,
    cancel: CancelToken,
) -> dict[tuple[int, int, str], dict[str, Any]]:
    """모든 비행의 분광 밴드 파일 픽셀 통계. 지상 캡처는 패널 힌트(bright_region)까지 계산."""
    key_of = {str(e.path): e.key for e, _ in records}
    sat_dn = sensor.camera.saturation_dn
    stride, bins = config.exposure.stride, config.exposure.histogram_bins
    tasks: list[PixelTask] = []
    owner: dict[str, tuple[int, int, str]] = {}
    for w in works:
        for i, c in enumerate(w.flight.captures):
            bright = w.is_ground[i]
            for b in sensor.spectral_band_keys:
                bm = c.bands.get(b)
                if bm is None or bm.path.suffix.lower() not in {".tif", ".tiff"}:
                    continue
                fkey = key_of.get(str(bm.path), str(bm.path))
                key = pixel_cache_key(fkey, sat_dn, stride, bins, bright)
                tasks.append(PixelTask(key, str(bm.path), sat_dn, stride, bins, bright))
                owner[key] = (w.flight.index, i, b)
    out: dict[tuple[int, int, str], dict[str, Any]] = {}
    cached = cache.get_pixels([t.key for t in tasks]) if cache else {}
    todo = []
    for t in tasks:
        v = cached.get(t.key)
        if v is not None:
            out[owner[t.key]] = v
        else:
            todo.append(t)
    _emit(on_progress, "pixels", len(out), len(tasks), f"캐시 적중 {len(out)}")
    if not todo:
        return out
    base = len(out)
    pending: list[tuple[str, dict[str, Any]]] = []

    def _prog(done: int, _total: int, name: str) -> None:
        _emit(on_progress, "pixels", base + done, len(tasks), name)

    for t, v in run_pixel_tasks(
        todo,
        workers=config.exposure.resolve_workers(),
        on_progress=_prog,
        cancel=cancel,
    ):
        v["stride"] = t.stride
        out[owner[t.key]] = v
        pending.append((t.key, v))
        if cache and len(pending) >= 500:
            cache.put_pixels(pending)
            pending = []
    if cache and pending:
        cache.put_pixels(pending)
    return out


def _summary(w: _FlightWork) -> FlightSummary:
    fl, traj, caps = w.flight, w.traj, w.flight.captures
    dur = None
    if fl.start_utc and fl.end_utc:
        dur = (fl.end_utc - fl.start_utc).total_seconds()
    valid_iv = sorted(v for v in w.intervals if v is not None and v > 0)
    offset = next((c.utc_offset_min for c in caps if c.utc_offset_min is not None), None)
    return FlightSummary(
        flight_id=fl.flight_id,
        index=fl.index,
        n_captures=len(caps),
        n_complete=sum(1 for c in caps if c.is_complete),
        start_utc=fl.start_utc,
        end_utc=fl.end_utc,
        duration_s=dur,
        utc_offset_min=offset,
        n_lines=sum(1 for ln in traj.lines if not ln.is_transit),
        mean_agl_m=round(traj.mean_agl_m, 1) if traj.mean_agl_m is not None else None,
        median_interval_s=valid_iv[len(valid_iv) // 2] if valid_iv else None,
        median_speed_mps=(
            round(traj.median_speed_mps, 2) if traj.median_speed_mps is not None else None
        ),
        track_length_m=traj.track_length_m,
        bbox=traj.bbox,
    )


def _apply_utc_offset(captures: list[CaptureMeta]) -> None:
    """태그에 로컬 시각이 없는 센서(RedEdge)는 다른 캡처 값 또는 PC 로컬 시간대를 쓴다."""
    known = next((c.utc_offset_min for c in captures if c.utc_offset_min is not None), None)
    if known is None:
        local = datetime.now().astimezone().utcoffset()
        known = int(local.total_seconds() // 60) if local is not None else 0
    for c in captures:
        if c.utc_offset_min is None:
            c.utc_offset_min = known


def _sample_tags(records: list[tuple[FileEntry, Raw]]) -> Raw | None:
    for _, raw in records:
        if "ExifTool:Error" not in raw:
            return {k: v for k, v in raw.items() if not k.startswith(("System:", "SourceFile"))}
    return None


def _empty_report(
    discovery: DiscoveryResult,
    config: Config,
    exiftool_version: str | None,
    headline: str,
    timings: dict[str, float],
    warnings: list[str],
) -> RunReport:
    excluded: list[ExcludedFile] = []
    return RunReport(
        generated_at=datetime.now(UTC),
        flightqa_version=__version__,
        exiftool_version=exiftool_version,
        root=discovery.root,
        sensor=None,
        sensor_display=None,
        discovery=discovery,
        n_captures=0,
        excluded=excluded,
        flights=[],
        status=Status.NA,
        headline="판정 불가",
        headline_detail=headline,
        next_actions=[
            "폴더에 RedEdge (IMG_XXXX_B.tif) 또는 M3M (DJI_..._MS_X.TIF) 파일이 있는지 확인"
        ],
        config=config.model_dump(mode="json"),
        timings_s={k: round(v, 3) for k, v in timings.items()},
        warnings=warnings,
    )
