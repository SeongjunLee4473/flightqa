"""설정 스키마와 로딩.

``default.toml`` 이 단일 기본값이고, 사용자 TOML 이 그 위에 깊은 병합된다.
임계값을 코드에 하드코딩하지 않는다 (CLAUDE.md §4, §12).
"""

from __future__ import annotations

import os
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flightqa.core.models import CameraModel, SensorKind


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscoveryConfig(_Strict):
    flight_gap_min: float = 10.0
    min_captures_per_flight: int = 5
    follow_symlinks: bool = False


class IntegrityConfig(_Strict):
    missing_band_warn_pct: float = 0.5
    missing_band_fail_pct: float = 5.0
    interval_outlier_factor: float = 2.0
    gap_warn_count: int = 1
    gap_fail_count: int = 10
    clock_reversal_warn_count: int = 1
    time_mismatch_tolerance_s: float = 120.0
    time_mismatch_warn_pct: float = 5.0
    corrupt_file_warn_count: int = 1
    corrupt_file_fail_pct: float = 2.0
    duplicate_warn_count: int = 1


class FlightConfig(_Strict):
    target_agl_m: float | Literal["auto"] = "auto"
    """목표 비행고도(m). "auto" = 임무 KMZ 의 촬영 고도, 없으면 조사 라인 중앙값(일관성만 판정)."""
    agl_tolerance_warn_pct: float = 20.0
    agl_tolerance_fail_pct: float = 30.0
    agl_within_tolerance_min_pct: float = 90.0
    agl_within_tolerance_fail_pct: float = 70.0
    altitude_std_warn_m: float = 5.0
    altitude_std_fail_m: float = 10.0
    target_speed_mps: float | Literal["auto"] = "auto"
    """목표 비행속도(m/s). "auto" = 임무 KMZ 의 비행속도, 없으면 중앙값(정보만)."""
    speed_tolerance_warn_pct: float = 25.0
    speed_tolerance_fail_pct: float = 50.0
    nadir_warn_deg: float = 5.0
    nadir_fail_deg: float = 10.0
    off_nadir_max_pct: float = 10.0
    oblique_min_deg: float = 30.0
    """이 각도 이상 기울어진 캡처가 ``oblique_min_run`` 개 이상 이어지면 의도된 경사 촬영
    (DJI 고도 최적화 등)으로 보아 연직 이탈·조사 라인·중복도에서 제외한다."""
    oblique_min_run: int = 3
    min_captures_per_line: int = 4
    line_heading_break_deg: float = 45.0
    takeoff_alt_msl_m: float | Literal["auto"] = "auto"
    dem_path: str = ""
    """지표고도 GeoTIFF (DEM/DSM). 주어지면 실제 AGL = 기체 고도 − geoid_offset − 지표고도."""
    geoid_offset_m: float = 0.0
    """기체 고도(타원체고, DJI RtkAlt) 와 DEM 정표고의 차 (N = h − H). 한반도 중부 약 +25 m."""
    terrain_follow_detect: bool = True
    """이륙점 기준 고도가 지형을 따라 오르내리는(지형추종) 비행을 감지해 AGL 판정을 N/A 로 둔다."""
    terrain_follow_min_range_m: float = 5.0
    """라인 안 고도 변화폭(p95−p5) 중앙값이 이 이상이고"""
    terrain_follow_corr_min: float = 0.8
    """인접 라인 고도 프로파일 상관계수 중앙값이 이 이상이면 지형추종으로 본다."""


class RtkConfig(_Strict):
    fixed_flags: list[int] = Field(default_factory=lambda: [50])
    fixed_min_pct_warn: float = 95.0
    fixed_min_pct_fail: float = 80.0
    std_hgt_warn_m: float = 0.10


class JudgementConfig(_Strict):
    warn_to_fail_count: int = 6


class CacheConfig(_Strict):
    enabled: bool = True
    path: str = ""

    def resolve_path(self) -> Path:
        if self.path:
            return Path(self.path).expanduser()
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
        return root / "flightqa" / "cache.sqlite"


class ExtractConfig(_Strict):
    workers: int = 0
    chunk_size: int = 50

    def resolve_workers(self) -> int:
        if self.workers > 0:
            return self.workers
        return max(1, min(os.cpu_count() or 2, 8))


class SensorConfig(_Strict):
    sensor_width_mm: float
    sensor_height_mm: float
    focal_length_mm: float
    image_width_px: int
    image_height_px: int
    saturation_dn: int = 65520

    def camera(self) -> CameraModel:
        return CameraModel(
            sensor_width_mm=self.sensor_width_mm,
            sensor_height_mm=self.sensor_height_mm,
            focal_length_mm=self.focal_length_mm,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            saturation_dn=self.saturation_dn,
        )


class OverlapConfig(_Strict):
    forward_warn_pct: float = 75.0
    forward_fail_pct: float = 60.0
    side_warn_pct: float = 60.0
    side_fail_pct: float = 40.0
    pairs_ok_min_pct: float = 90.0
    hole_min_area_m2: float = 50.0


class SurveyAreaConfig(_Strict):
    """궤적 기반 조사 구역 추정과 GIS 내보내기 (조정기 임무 폴리곤은 영상에 없으므로 추정)."""

    enabled: bool = True
    mission_path: str = ""
    """조정기에서 내보낸 임무 KMZ/KML. 비어 있으면 데이터 폴더 안에서 찾는다."""
    auto_mission_files: bool = True
    """데이터 폴더(깊이 3) 의 *.kmz / *.kml 을 임무 파일로 자동 인식."""
    mission_match_min_pct: float = 50.0
    """임무 파일을 비행에 붙이려면 조사 라인 캡처의 이 비율 이상이 폴리곤 안에 있어야 한다."""
    hull_ratio: float = 0.3
    """shapely concave_hull ratio: 0 = 가장 오목, 1 = 볼록 껍질."""
    margin_frac: float = 0.0
    """여유 폭 = 캡처 지상 커버리지의 짧은 변 × 이 비율. 실제 Pilot 2 매핑 임무에서는 바깥 라인이
    폴리곤 경계에 놓여 0 이 가장 잘 맞았다 (ASSUMPTIONS 2026-09-30, IoU 0.88)."""
    margin_m: float = 0.0
    """0 이면 margin_frac 로 자동, 양수면 이 값(m) 을 그대로 쓴다."""
    min_points: int = 4
    fill_warn_pct: float = 95.0
    fill_fail_pct: float = 85.0
    uncovered_min_area_m2: float = 50.0
    raster_cell_m: float = 2.0
    raster_max_pixels: int = 16_000_000
    """중복 횟수 래스터 픽셀 상한. 넘으면 셀 크기를 키운다."""


class MetashapeConfig(_Strict):
    """Metashape 처리 보고서(PDF) 판정 (docs/QA_RULES.md "후처리 품질")."""

    aligned_warn_pct: float = 100.0
    aligned_fail_pct: float = 95.0
    reproj_rms_warn_px: float = 1.0
    reproj_rms_fail_px: float = 1.5
    reproj_max_warn_px: float = 30.0
    multiplicity_warn: float = 3.0
    calib_corr_warn: float = 0.95
    location_profile: str = "auto"
    """auto | rtk | gps — 카메라 위치 오차 기준. auto 는 카메라 이름으로 추정."""
    rtk_xy_warn_m: float = 0.10
    rtk_xy_fail_m: float = 0.30
    rtk_z_warn_m: float = 0.15
    rtk_z_fail_m: float = 0.50
    gps_xy_warn_m: float = 3.0
    gps_xy_fail_m: float = 10.0
    gps_z_warn_m: float = 5.0
    gps_z_fail_m: float = 15.0
    gcp_xy_gsd_mult: float = 2.0
    gcp_z_gsd_mult: float = 3.0
    cross_images_warn_pct: float = 1.0
    cross_images_fail_pct: float = 10.0
    cross_dev_warn_pct: float = 20.0
    cross_area_warn_pct: float = 50.0
    warn_to_fail_count: int = 4


class ExposureConfig(_Strict):
    stride: int = 1
    workers: int = 0
    saturated_pixel_warn_pct: float = 0.5
    saturated_pixel_fail_pct: float = 5.0
    saturated_captures_warn_pct: float = 3.0
    saturated_captures_fail_pct: float = 15.0
    underexposed_range_pct: float = 5.0
    underexposed_captures_warn_pct: float = 5.0
    exposure_jump_factor: float = 2.0
    exposure_jumps_warn_count: int = 3
    histogram_bins: int = 64

    def resolve_workers(self) -> int:
        if self.workers > 0:
            return self.workers
        return max(1, min(os.cpu_count() or 2, 8))


class IrradianceConfig(_Strict):
    cv_warn_pct: float = 10.0
    cv_fail_pct: float = 25.0
    drop_factor: float = 0.75
    drop_window: int = 15
    events_warn_count: int = 1
    events_fail_count: int = 5
    stable_fraction_warn_pct: float = 80.0
    stable_band_pct: float = 10.0
    solar_elevation_min_deg: float = 30.0
    low_sun_warn_pct: float = 10.0
    low_sun_fail_pct: float = 50.0
    xmp_solar_mismatch_warn_deg: float = 5.0
    symmetry_warn_pct: float = 20.0


class PanelConfig(_Strict):
    required_for: list[str] = Field(default_factory=lambda: ["rededge_mx"])

    def required(self, sensor: str) -> bool:
        return sensor in self.required_for

    ground_agl_max_m: float = 5.0
    window_min: float = 20.0
    detect_bright_region: bool = True
    bright_fraction_min: float = 0.01
    bright_fraction_max: float = 0.5


class ReportConfig(_Strict):
    """보고서 옵션. 배경지도는 온라인 타일이라 기본 꺼짐 (CLAUDE.md §5.1·§12: 오프라인 원칙)."""

    online_basemap: bool = False
    """true 면 HTML 을 열 때 위성 배경을 자동으로 불러온다. false 여도 버튼으로 켤 수 있다."""
    basemap_provider: Literal["esri", "vworld", "custom"] = "esri"
    basemap_url: str = ""
    """custom 일 때 XYZ 타일 URL 템플릿 ({z}/{x}/{y}, 필요하면 {key})."""
    basemap_api_key: str = ""
    """vworld 등 키가 필요한 제공자용."""
    basemap_attribution: str = ""
    """custom 제공자 출처 표기. esri/vworld 는 내장."""
    basemap_max_zoom: int = 19


class Config(_Strict):
    report: ReportConfig = Field(default_factory=ReportConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    integrity: IntegrityConfig = Field(default_factory=IntegrityConfig)
    flight: FlightConfig = Field(default_factory=FlightConfig)
    rtk: RtkConfig = Field(default_factory=RtkConfig)
    judgement: JudgementConfig = Field(default_factory=JudgementConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    extract: ExtractConfig = Field(default_factory=ExtractConfig)
    overlap: OverlapConfig = Field(default_factory=OverlapConfig)
    survey_area: SurveyAreaConfig = Field(default_factory=SurveyAreaConfig)
    metashape: MetashapeConfig = Field(default_factory=MetashapeConfig)
    exposure: ExposureConfig = Field(default_factory=ExposureConfig)
    irradiance: IrradianceConfig = Field(default_factory=IrradianceConfig)
    panel: PanelConfig = Field(default_factory=PanelConfig)
    sensors: dict[str, SensorConfig] = Field(default_factory=dict)

    def sensor(self, kind: SensorKind) -> SensorConfig:
        try:
            return self.sensors[kind.value]
        except KeyError as e:
            raise KeyError(f"설정에 [sensors.{kind.value}] 섹션이 없습니다") from e


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_config_dict() -> dict[str, Any]:
    text = resources.files("flightqa.config").joinpath("default.toml").read_text(encoding="utf-8")
    return tomllib.loads(text)


def load_config(user_path: str | os.PathLike[str] | None = None) -> Config:
    """기본값 + (선택) 사용자 TOML. 알 수 없는 키는 오류 (오타를 조용히 무시하지 않는다)."""
    data = default_config_dict()
    if user_path is not None:
        p = Path(user_path)
        with p.open("rb") as fh:
            data = _deep_merge(data, tomllib.load(fh))
    return Config.model_validate(data)
