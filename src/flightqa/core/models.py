"""공통 데이터 스키마 (pydantic v2).

센서별 태그를 정규화한 ``CaptureMeta`` 와, 분석·판정 결과를 담는 ``RunReport`` 계열이다.
JSON 내보내기(summary.json)의 스키마이기도 하므로 필드 변경 시 ``SCHEMA_VERSION`` 을 올린다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.4"


class SensorKind(StrEnum):
    REDEDGE_MX = "rededge_mx"
    DJI_M3M = "dji_m3m"


class Status(StrEnum):
    """판정 상태. 심각도 순서는 PASS = N/A < WARN < FAIL."""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NA = "N/A"


_SEVERITY = {Status.PASS: 0, Status.NA: 0, Status.WARN: 1, Status.FAIL: 2}


def worst(statuses: list[Status]) -> Status:
    """여러 판정 중 가장 나쁜 것. 비어 있으면 N/A, 전부 N/A 면 N/A."""
    if not statuses:
        return Status.NA
    top = max(statuses, key=lambda s: _SEVERITY[s])
    if top is Status.PASS and all(s is Status.NA for s in statuses):
        return Status.NA
    if top is Status.NA and Status.PASS in statuses:
        return Status.PASS
    return top


# --------------------------------------------------------------------------- 센서 사양


class BandSpec(BaseModel):
    """밴드 정의. ``key`` 는 내부 식별자(파일 접미사 기준), ``name`` 은 태그의 BandName."""

    key: str
    name: str
    center_nm: float
    fwhm_nm: float | None = None
    rig_index: int | None = None
    is_rgb: bool = False


class CameraModel(BaseModel):
    """GSD·중복도 계산용 카메라 물리값."""

    sensor_width_mm: float
    sensor_height_mm: float
    focal_length_mm: float
    image_width_px: int
    image_height_px: int
    bit_depth: int = 16
    saturation_dn: int = 65520

    def gsd_m(self, agl_m: float) -> float:
        """지상표본거리 (m/px)."""
        return (self.sensor_width_mm / self.image_width_px) * agl_m / self.focal_length_mm

    def footprint_m(self, agl_m: float) -> tuple[float, float]:
        """지상 커버리지 (가로, 세로) m."""
        w = self.sensor_width_mm * agl_m / self.focal_length_mm
        h = self.sensor_height_mm * agl_m / self.focal_length_mm
        return w, h


# --------------------------------------------------------------------------- 캡처 메타


class ExposureInfo(BaseModel):
    exposure_s: float | None = None
    iso: float | None = None
    gain: float | None = None


class BandMeta(BaseModel):
    """밴드 파일 1 개의 메타."""

    band: str
    path: Path
    size_bytes: int | None = None
    mtime_utc: datetime | None = None
    time_utc: datetime | None = None
    exposure: ExposureInfo = Field(default_factory=ExposureInfo)
    irradiance: float | None = None
    """조도. RedEdge: DLS HorizontalIrradiance (W/m²/nm). M3M: 노출·게인 정규화 카운트."""
    irradiance_raw: float | None = None
    black_level: float | None = None
    width_px: int | None = None
    height_px: int | None = None
    bits: int | None = None


class RtkInfo(BaseModel):
    status: str | None = None
    flag: int | None = None
    fixed: bool | None = None
    std_lat_m: float | None = None
    std_lon_m: float | None = None
    std_hgt_m: float | None = None
    diff_age_s: float | None = None


class CaptureMeta(BaseModel):
    """센서 공통 캡처 메타. 없는 정보는 None, 센서 고유 정보는 ``extras``."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    capture_id: str
    sensor: SensorKind
    index: int = -1
    group: str | None = None
    """RedEdge: SET/하위폴더(예: 0000SET/000). M3M: 미션 폴더명."""

    time_utc: datetime | None = None
    utc_offset_min: int | None = None
    lat: float | None = None
    lon: float | None = None
    alt_msl_m: float | None = None
    alt_rel_m: float | None = None
    """이륙점 기준 상대고도 (M3M RelativeAltitude). RedEdge 는 None → 분석 단계에서 추정."""
    pressure_alt_m: float | None = None

    yaw_deg: float | None = None
    pitch_deg: float | None = None
    roll_deg: float | None = None
    gimbal_pitch_deg: float | None = None
    gimbal_roll_deg: float | None = None
    gimbal_yaw_deg: float | None = None

    solar_elevation_deg: float | None = None
    solar_azimuth_deg: float | None = None

    trigger: str | None = None
    flight_tag: str | None = None
    model: str | None = None
    firmware: str | None = None

    gps_xy_accuracy_m: float | None = None
    gps_z_accuracy_m: float | None = None
    rtk: RtkInfo | None = None

    bands: dict[str, BandMeta] = Field(default_factory=dict)
    missing_bands: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_bands

    @property
    def primary_path(self) -> Path | None:
        for b in self.bands.values():
            return b.path
        return None

    def mean_irradiance(self) -> float | None:
        vals = [b.irradiance for b in self.bands.values() if b.irradiance is not None]
        return sum(vals) / len(vals) if vals else None


class Flight(BaseModel):
    flight_id: str
    index: int
    captures: list[CaptureMeta]

    @property
    def start_utc(self) -> datetime | None:
        times = [c.time_utc for c in self.captures if c.time_utc]
        return min(times) if times else None

    @property
    def end_utc(self) -> datetime | None:
        times = [c.time_utc for c in self.captures if c.time_utc]
        return max(times) if times else None


# --------------------------------------------------------------------------- 발견·제외


class ExcludedFile(BaseModel):
    path: Path
    reason: str


class DiscoveryResult(BaseModel):
    root: Path
    sensor: SensorKind | None
    confidence: float
    scores: dict[str, float] = Field(default_factory=dict)
    n_files_scanned: int = 0
    image_files: list[Path] = Field(default_factory=list)
    n_provisional_captures: int = 0
    groups: dict[str, int] = Field(default_factory=dict)
    """그룹(SET 폴더/미션 폴더)별 파일 수."""
    unresolved: list[Path] = Field(default_factory=list)
    """이미지이지만 파일명 규칙에 안 맞는 파일 (태그로 재편입 시도)."""
    ignored: list[ExcludedFile] = Field(default_factory=list)


# --------------------------------------------------------------------------- 분석·판정 결과


class Metric(BaseModel):
    """모든 항목은 측정값·기준값·판정·근거를 갖는다 (CLAUDE.md §4)."""

    key: str
    label: str
    value: float | int | str | None = None
    unit: str | None = None
    threshold: str | None = None
    status: Status = Status.NA
    reason: str = ""
    next_action: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    gate: bool = False
    """True 면 FAIL 시 전체를 '재비행 검토' 로 만든다."""


class CheckGroup(BaseModel):
    key: str
    label: str
    metrics: list[Metric] = Field(default_factory=list)

    @property
    def status(self) -> Status:
        return worst([m.status for m in self.metrics])


class ActionItem(BaseModel):
    """권고 조치 1 건 (WARN·FAIL 항목의 next_action). GUI·보고서가 구조적으로 그린다."""

    flight_id: str | None = None
    """None 이면 데이터셋 단위 항목 (파일 상태 등)."""
    flight_index: int | None = None
    group_key: str
    group_label: str
    metric_key: str
    metric_label: str
    status: Status
    text: str


class LineInfo(BaseModel):
    index: int
    start_capture: int
    end_capture: int
    n_captures: int
    heading_deg: float
    length_m: float
    mean_agl_m: float | None
    std_agl_m: float | None
    mean_speed_mps: float | None
    is_transit: bool = False


class FlightSummary(BaseModel):
    flight_id: str
    index: int
    n_captures: int
    n_complete: int
    start_utc: datetime | None
    end_utc: datetime | None
    duration_s: float | None
    utc_offset_min: int | None
    n_lines: int
    mean_agl_m: float | None
    median_interval_s: float | None
    median_speed_mps: float | None
    track_length_m: float | None
    bbox: list[float] | None = None
    """[min_lon, min_lat, max_lon, max_lat]"""


class CaptureRow(BaseModel):
    """캡처별 계산 결과 (captures.csv 의 한 행, 차트 시계열의 원천)."""

    index: int
    capture_id: str
    flight_index: int
    time_utc: datetime | None
    lat: float | None
    lon: float | None
    x_m: float | None
    y_m: float | None
    alt_msl_m: float | None
    agl_m: float | None
    speed_mps: float | None
    heading_deg: float | None
    interval_s: float | None
    line: int | None
    off_nadir_deg: float | None
    n_bands: int
    missing_bands: str
    rtk_fixed: bool | None
    irradiance: float | None
    solar_elevation_deg: float | None
    flags: str = ""
    forward_overlap_pct: float | None = None
    gsd_cm: float | None = None
    sat_max_pct: float | None = None
    """밴드 중 최대 포화 픽셀 비율 (%)."""
    underexposed: bool | None = None
    is_ground: bool = False
    solar_elevation_calc_deg: float | None = None
    primary_file: str | None = None
    """첫 분광 밴드 파일 경로 (썸네일용)."""


class PixelRow(BaseModel):
    """캡처·밴드별 픽셀 통계 (saturation.csv 의 한 행)."""

    index: int
    capture_id: str
    band: str
    file: str
    sat_pct: float
    p1: float
    p99: float
    mean: float
    n_pixels: int
    stride: int


class Coverage(BaseModel):
    area_m2: float
    n_holes: int
    holes_area_m2: float
    hole_centroids: list[list[float]] = Field(default_factory=list)
    """[lon, lat] 목록."""
    geojson: dict[str, Any] | None = None
    """커버리지 합집합 (WGS84 MultiPolygon)."""
    epsg: int | None = None
    rings_m: list[list[list[float]]] = Field(default_factory=list)
    """보고서 지도용: UTM m 단위 외곽 링 목록 [[x, y], ...] (단순화)."""
    hole_rings_m: list[list[list[float]]] = Field(default_factory=list)


class MissionPlan(BaseModel):
    """조정기에서 내보낸 DJI 임무 파일(KMZ/WPML) 의 요약 (schema 1.4, docs/tags_dji_wpml.md)."""

    source: str
    """파일 이름."""
    wpml_version: str | None = None
    template_type: str | None = None
    """mapping2d 등."""
    created_utc: datetime | None = None
    drone_enum: int | None = None
    payload_enum: int | None = None
    height_mode: str | None = None
    height_m: float | None = None
    shoot_height_m: float | None = None
    margin_m: float | None = None
    """폴리곤 바깥으로 라인을 늘리는 여유 (m)."""
    overlap_h_pct: int | None = None
    overlap_w_pct: int | None = None
    direction_deg: float | None = None
    speed_mps: float | None = None
    shoot_type: str | None = None
    elevation_optimize: bool = False
    image_format: str | None = None
    takeoff_ref: list[float] | None = None
    """[lat, lon, alt] — 파일의 takeOffRefPoint 순서 그대로."""
    polygon_geojson: dict[str, Any]
    """매핑 폴리곤 (WGS84 Polygon | MultiPolygon)."""
    n_polygon_vertices: int = 0
    waypoints_geojson: dict[str, Any] | None = None
    """웨이포인트 순서 (WGS84 LineString)."""
    n_waypoints: int = 0
    distance_m: float | None = None
    duration_s: float | None = None


class SurveyArea(BaseModel):
    """조사 구역 (schema 1.4): 임무 파일 폴리곤 또는 궤적 추정.

    조정기에서 그린 임무 폴리곤은 영상 메타데이터에 없다. KMZ 가 있으면 그 폴리곤을 쓰고
    (``method = "mission_kmz"``), 없으면 조사 라인 캡처점의 오목 껍질에 여유 폭을 더해 추정한다
    (``method = "trajectory_hull"``).
    """

    method: str = "trajectory_hull"
    source: str | None = None
    """임무 파일 이름 (method = mission_kmz 일 때)."""
    n_points: int
    hull_ratio: float
    margin_m: float
    area_m2: float
    covered_area_m2: float
    uncovered_area_m2: float
    fill_pct: float | None = None
    """구역 안에서 커버리지가 덮는 비율 (%)."""
    n_uncovered: int = 0
    """면적 기준 이상인 미촬영 조각 수."""
    n_outside: int | None = None
    """임무 폴리곤 밖 조사 라인 캡처 수 (mission_kmz 일 때)."""
    outside_pct: float | None = None
    epsg: int | None = None
    geojson: dict[str, Any] | None = None
    """추정 구역 (WGS84 Polygon)."""
    uncovered_geojson: dict[str, Any] | None = None
    """구역 안 미촬영 부분 (WGS84 MultiPolygon)."""
    rings_m: list[list[list[float]]] = Field(default_factory=list)
    """보고서 지도용 UTM 외곽 링."""
    bbox: list[float] | None = None
    """[min_lon, min_lat, max_lon, max_lat]"""


class PanelInfo(BaseModel):
    pre_indices: list[int] = Field(default_factory=list)
    post_indices: list[int] = Field(default_factory=list)
    panel_like_indices: list[int] = Field(default_factory=list)
    saturated_indices: list[int] = Field(default_factory=list)


class FlightReport(BaseModel):
    summary: FlightSummary
    status: Status
    groups: list[CheckGroup]
    lines: list[LineInfo]
    rows: list[CaptureRow]
    takeoff_alt_msl_m: float | None = None
    agl_source: str = ""
    terrain_follow: bool = False
    """지형추종 비행으로 추정되어 AGL 판정을 보류했는지 (schema 1.2)."""
    pixel_rows: list[PixelRow] = Field(default_factory=list)
    band_hist: dict[str, list[int]] = Field(default_factory=dict)
    hist_edges: list[int] = Field(default_factory=list)
    coverage: Coverage | None = None
    survey_area: SurveyArea | None = None
    panel: PanelInfo | None = None


class RunReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    flightqa_version: str
    exiftool_version: str | None
    root: Path
    sensor: SensorKind | None
    sensor_display: str | None
    discovery: DiscoveryResult
    n_captures: int
    excluded: list[ExcludedFile]
    run_groups: list[CheckGroup] = Field(default_factory=list)
    """데이터셋 단위 판정 (파일 상태 등)."""
    flights: list[FlightReport]
    status: Status
    headline: str
    """짧은 결론 (예: '재비행 검토 필요')."""
    headline_detail: str = ""
    """결론의 근거 한 줄 (예: '게이트 항목 실패 2건: A, B')."""
    next_actions: list[str]
    """권고 조치 (텍스트, CLI·호환용). 구조는 ``action_items``."""
    action_items: list[ActionItem] = Field(default_factory=list)
    config: dict[str, Any]
    sample_tags: dict[str, Any] | None = None
    mission: MissionPlan | None = None
    """조정기 임무 파일 요약 (첫 번째로 쓰인 것; 호환용)."""
    missions: list[MissionPlan] = Field(default_factory=list)
    """비행에 실제로 쓰인 임무 파일들 (비행마다 폴리곤이 겹치는 파일을 고른다)."""
    timings_s: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
