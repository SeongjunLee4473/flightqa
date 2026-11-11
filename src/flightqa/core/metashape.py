"""Agisoft Metashape 처리 보고서(PDF) 읽기와 후처리 품질 판정.

근거: docs/metashape_report.md — Metashape 2.3.1 "Generate Report" PDF 두 종(RGB 1 카메라, RedEdge
다중분광 5 카메라)의 실제 텍스트 구조. PDF 는 Qt 가 만든 텍스트 PDF 라 ``pypdf`` 로 글자가 그대로
나오며, 다음 규칙으로 정규화한다.

- 그림자 글씨 때문에 제목·라벨 줄이 두 번씩 나온다 → 연속 중복 줄을 하나로.
- 일부 라벨은 글자 사이에 공백이 끼어 나온다 ("T ie Points", "Depth M aps", "V a lu e") → 라벨
  비교는 공백을 뺀 소문자 키로 한다.
- 값은 라벨 다음 줄이다. 같은 라벨이 섹션마다 반복되므로("Processing time" 등) 섹션 제목 사이
  범위 안에서만 찾는다.
- 보정 계수 표는 "이름, 이름, 값, 값, (오차), (1.00, 상관…)" 순으로 나온다. 고정된 계수(예:
  다중분광의 K3)는 오차·상관이 없고, 열 순서는 머리글 순서를 따른다.

판정은 비행 분석과 같은 틀(:class:`Metric` / :class:`CheckGroup`)이고 임계값은 설정
``[metashape]`` 에서 읽는다. 근거는 docs/QA_RULES.md "후처리 품질".
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from flightqa.config.schema import MetashapeConfig
from flightqa.core.models import CheckGroup, Metric, RunReport, Status, worst
from flightqa.core.rules import flight_status, fmt, grade_max, grade_min, info_metric

log = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


class MetashapeError(ValueError):
    """Metashape 보고서가 아니거나 읽을 수 없다."""


# --------------------------------------------------------------------------- 모델


class CalibCoef(BaseModel):
    value: float
    error: float | None = None
    """None 이면 고정 계수 (조정되지 않음)."""


class CameraInfo(BaseModel):
    name: str
    """보고서의 카메라 이름 (예: "M3M (12.29mm)", "RedEdge-M, Blue (5.5mm)")."""
    band: str | None = None
    """다중분광이면 밴드 이름 (쉼표 뒤)."""
    width_px: int | None = None
    height_px: int | None = None
    focal_mm: float | None = None
    pixel_um: float | None = None
    precalibrated: bool | None = None
    n_images: int | None = None
    coefficients: dict[str, CalibCoef] = Field(default_factory=dict)
    correlations: dict[str, float] = Field(default_factory=dict)
    """계수 쌍 "K1-K2" → 상관계수 (상삼각)."""

    @property
    def max_abs_correlation(self) -> tuple[str, float] | None:
        if not self.correlations:
            return None
        k = max(self.correlations, key=lambda p: abs(self.correlations[p]))
        return k, self.correlations[k]


class LocationError(BaseModel):
    unit: str = "m"
    x_m: float | None = None
    y_m: float | None = None
    z_m: float | None = None
    xy_m: float | None = None
    total_m: float | None = None


class GcpSummary(BaseModel):
    kind: str
    """control | check"""
    count: int | None = None
    x_m: float | None = None
    y_m: float | None = None
    z_m: float | None = None
    xy_m: float | None = None
    total_m: float | None = None


class MetashapeReport(BaseModel):
    """보고서 한 부의 구조화 결과. 좌표는 들어 있지 않다 (보고서 자체에 없음)."""

    schema_version: str = SCHEMA_VERSION
    source: str
    report_date: str | None = None
    software_version: str | None = None
    n_images: int | None = None
    n_aligned: int | None = None
    flying_altitude_m: float | None = None
    gsd_cm: float | None = None
    coverage_km2: float | None = None
    camera_stations: int | None = None
    tie_points: int | None = None
    tie_points_total: int | None = None
    projections: int | None = None
    reprojection_error_px: float | None = None
    rms_reprojection_px: float | None = None
    rms_reprojection_norm: float | None = None
    max_reprojection_px: float | None = None
    mean_keypoint_size_px: float | None = None
    tie_point_multiplicity: float | None = None
    point_colors: str | None = None
    cameras: list[CameraInfo] = Field(default_factory=list)
    location_error: LocationError | None = None
    gcp: list[GcpSummary] = Field(default_factory=list)
    alignment: dict[str, str] = Field(default_factory=dict)
    """정렬 파라미터 (Accuracy, Key point limit, …) 원문."""
    depth_maps: dict[str, str] = Field(default_factory=dict)
    dem: dict[str, str] = Field(default_factory=dict)
    orthomosaic: dict[str, str] = Field(default_factory=dict)
    raster_transform: str | None = None
    system: dict[str, str] = Field(default_factory=dict)
    coordinate_system: str | None = None
    n_pages: int = 0
    figures: list[str] = Field(default_factory=list)
    """보고서에 들어 있는 그림 캡션 (Fig. N. …)."""
    warnings: list[str] = Field(default_factory=list)

    @property
    def n_bands(self) -> int:
        return max(1, len(self.cameras))

    @property
    def is_multispectral(self) -> bool:
        return len(self.cameras) >= 2 or any(c.band for c in self.cameras)

    @property
    def aligned_pct(self) -> float | None:
        if self.n_images and self.n_aligned is not None:
            return self.n_aligned / self.n_images * 100.0
        return None


class MetashapeResult(BaseModel):
    """보고서 + 판정 (내보내기·GUI 단위)."""

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    report: MetashapeReport
    groups: list[CheckGroup]
    status: Status
    headline: str
    headline_detail: str
    next_actions: list[str]
    flight_root: str | None = None
    """교차 검증에 쓴 FlightQA 분석 폴더."""


# --------------------------------------------------------------------------- 텍스트 추출·정규화


def _key(s: str) -> str:
    return re.sub(r"[\s:]+", "", s).lower()


def extract_lines(path: Path) -> tuple[list[str], int]:
    """PDF → 정규화한 줄 목록. (줄, 쪽 수). 그림자 중복 줄은 하나로 합친다."""
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - 의존성 누락
        raise MetashapeError("pypdf 가 설치되지 않음") from e
    try:
        reader = PdfReader(str(path))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:
        raise MetashapeError(f"PDF 를 읽을 수 없음 {path.name}: {e}") from e
    lines = normalize_lines("\n".join(pages).split("\n"))
    if not any("metashape" in ln.lower() for ln in lines[:40]):
        raise MetashapeError(f"Metashape 처리 보고서가 아님: {path.name}")
    return lines, len(pages)


def normalize_lines(raw: Sequence[str]) -> list[str]:
    out: list[str] = []
    for ln in raw:
        s = ln.strip()
        if not s:
            continue
        if out and out[-1] == s and not _is_number(s):
            continue  # 그림자 글씨 중복 (숫자는 값이 겹칠 수 있어 그대로 둔다)
        out.append(s)
    # 숫자 값도 '값, 값' 으로 두 번 나오는 표(보정 계수)가 있다 — 표 파서가 처리한다
    return out


_NUM_RE = re.compile(r"^[-+]?\d[\d,]*(?:\.\d+)?(?:e\s?[-+]?\d+)?$", re.IGNORECASE)


def _is_number(s: str) -> bool:
    return bool(_NUM_RE.match(s.replace(" ", "")))


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    t = s.replace(",", "").replace(" ", "")
    m = re.match(r"^[-+]?\d*\.?\d+(?:e[-+]?\d+)?", t, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _int(s: str | None) -> int | None:
    f = _num(s)
    return round(f) if f is not None else None


class _Doc:
    """정규화한 줄 목록에 대한 조회 도우미."""

    def __init__(self, lines: Sequence[str]) -> None:
        self.lines = list(lines)
        self.keys = [_key(ln) for ln in self.lines]

    def find(self, label: str, start: int = 0, end: int | None = None) -> int:
        k = _key(label)
        end = len(self.lines) if end is None else end
        for i in range(start, end):
            if self.keys[i] == k:
                return i
        return -1

    def value_after(self, label: str, start: int = 0, end: int | None = None) -> str | None:
        i = self.find(label, start, end)
        if i < 0 or i + 1 >= len(self.lines):
            return None
        return self.lines[i + 1]

    def section(self, title: str, next_titles: Sequence[str], start: int = 0) -> tuple[int, int]:
        """제목 줄 다음부터, 다음 제목들 중 가장 먼저 나오는 것 전까지. 없으면 (-1, -1)."""
        i = self.find(title, start)
        if i < 0:
            return -1, -1
        ends = [j for j in (self.find(t, i + 1) for t in next_titles) if j > i]
        return i + 1, (min(ends) if ends else len(self.lines))


# --------------------------------------------------------------------------- 파서

_COEF_RE = re.compile(r"^(F|Cx|Cy|K[1-4]|P\s?[1-4]|B[12])$")
_RES_RE = re.compile(r"^(\d[\d,]*)\s*x\s*(\d[\d,]*)$")
_PIX_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)$")
_MM_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*mm$")
_FIG_RE = re.compile(r"^Fig\.\s*\d+\.\s*(.+)$")
_RESID_RE = re.compile(r"^Fig\.\s*\d+\.\s*Image residuals for (.+?)\.?$")
_NIMG_RE = re.compile(r"^(\d[\d,]*)\s+images?,\s*(precalibrated|adjusted|.*)$", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{1,2}\s+[A-Z][a-z]+\s+\d{4}$")

SECTION_TITLES = (
    "Survey Data",
    "Camera Calibration",
    "Ground Control Points",
    "Camera Locations",
    "Digital Elevation Model",
    "Processing Parameters",
    "Reconstruction parameters",
    "Orthomosaic",
    "System",
)


def parse_lines(
    lines: Sequence[str], source: str = "report.pdf", n_pages: int = 0
) -> MetashapeReport:
    d = _Doc(lines)
    rep = MetashapeReport(source=source, n_pages=n_pages)
    rep.report_date = next((ln for ln in d.lines[:12] if _DATE_RE.match(ln)), None)
    rep.figures = [m.group(1) for ln in d.lines if (m := _FIG_RE.match(ln))]

    # ---- 조사 개요
    s, e = d.section(
        "Survey Data", ["Camera Calibration", "Camera Locations", "Processing Parameters"]
    )
    if s >= 0:
        rep.n_images = _int(d.value_after("Number of images", s, e))
        rep.flying_altitude_m = _num(d.value_after("Flying altitude", s, e))
        rep.gsd_cm = _num(d.value_after("Ground resolution", s, e))
        rep.coverage_km2 = _num(d.value_after("Coverage area", s, e))
        rep.camera_stations = _int(d.value_after("Camera stations", s, e))
        rep.tie_points = _int(d.value_after("Tie points", s, e))
        rep.projections = _int(d.value_after("Projections", s, e))
        rep.reprojection_error_px = _num(d.value_after("Reprojection error", s, e))
        rep.cameras = _parse_camera_table(d, s, e)
    else:
        rep.warnings.append("'Survey Data' 섹션이 없음")

    # ---- 카메라 보정 (카메라마다 한 쪽)
    _parse_calibrations(d, rep)

    # ---- GCP (샘플에는 없음 — 방어적으로)
    s, e = d.section(
        "Ground Control Points",
        ["Camera Locations", "Digital Elevation Model", "Processing Parameters"],
    )
    if s >= 0:
        rep.gcp = _parse_gcp(d, s, e)

    # ---- 카메라 위치 오차
    s, e = d.section(
        "Camera Locations",
        ["Digital Elevation Model", "Processing Parameters", "Ground Control Points"],
    )
    if s >= 0:
        rep.location_error = _parse_location_error(d, s, e)

    # ---- DEM 쪽
    s, e = d.section("Digital Elevation Model", ["Processing Parameters"])
    if s >= 0:
        for lab, key in (("Resolution", "resolution"), ("Point density", "point_density")):
            v = d.value_after(lab, s, e)
            if v:
                rep.dem[key] = v

    # ---- 처리 파라미터
    s, e = d.section("Processing Parameters", ["System"])
    if s >= 0:
        rep.n_aligned = _int(d.value_after("Aligned images", s, e))
        if rep.n_images is None:
            rep.n_images = _int(d.value_after("Images", s, e))
        rep.coordinate_system = d.value_after("Coordinate system", s, e)
        pts = d.value_after("Points", s, e)
        if pts and "of" in pts:
            a, b = pts.split("of", 1)
            rep.tie_points = rep.tie_points or _int(a)
            rep.tie_points_total = _int(b)
        rms = d.value_after("RMS reprojection error", s, e)
        if rms:
            rep.rms_reprojection_norm = _num(rms)
            m = re.search(r"\(([\d.]+)\s*pix\)", rms)
            rep.rms_reprojection_px = _num(m.group(1)) if m else None
        mx = d.value_after("Max reprojection error", s, e)
        if mx:
            m = re.search(r"\(([\d.]+)\s*pix\)", mx)
            rep.max_reprojection_px = _num(m.group(1)) if m else _num(mx)
        rep.mean_keypoint_size_px = _num(d.value_after("Mean key point size", s, e))
        rep.point_colors = d.value_after("Point colors", s, e)
        rep.tie_point_multiplicity = _num(d.value_after("Average tie point multiplicity", s, e))
        rep.software_version = d.value_after("Software version", s, e)
        a_s, a_e = d.section(
            "Alignment parameters", ["Depth Maps", "DEM", "Orthomosaic", "System"], s
        )
        if a_s >= 0:
            rep.alignment = _kv_block(d, a_s, a_e)
        dm_s, dm_e = d.section("Depth Maps", ["DEM", "Orthomosaic", "System"], s)
        if dm_s >= 0:
            rep.depth_maps = _kv_block(d, dm_s, dm_e)
        dem_s, dem_e = d.section("DEM", ["Orthomosaic", "System"], s)
        if dem_s >= 0:
            rep.dem.update(
                {k: v for k, v in _kv_block(d, dem_s, dem_e).items() if k not in rep.dem}
            )
        o_s, o_e = d.section("Orthomosaic", ["Raster Transform", "System"], s)
        if o_s >= 0:
            rep.orthomosaic = _kv_block(d, o_s, o_e)
        rt = d.value_after("Expression", s, e)
        if rt and "/" in rt:
            rep.raster_transform = rt
    else:
        rep.warnings.append("'Processing Parameters' 섹션이 없음")

    s, e = d.section("System", [])
    if s >= 0:
        rep.system = _kv_block(d, s, e)
        rep.software_version = rep.software_version or rep.system.get("Software version")
    return rep


_KV_SKIP = {"Depth maps generation parameters", "Reconstruction parameters"}


def _kv_block(d: _Doc, s: int, e: int) -> dict[str, str]:
    """'라벨 / 값' 이 번갈아 나오는 블록. 라벨은 글자로 시작하고 값은 그 다음 줄."""
    out: dict[str, str] = {}
    i = s
    while i + 1 < e:
        lab, val = d.lines[i], d.lines[i + 1]
        if lab in _KV_SKIP or lab.startswith("Page ") or lab.startswith("Table "):
            i += 1
            continue
        if re.match(r"^[A-Za-z][A-Za-z()/ .-]*$", lab) and not _is_number(lab):
            if lab not in out:
                out[lab] = val
            i += 2
        else:
            i += 1
    return out


def _parse_camera_table(d: _Doc, s: int, e: int) -> list[CameraInfo]:
    cams: list[CameraInfo] = []
    i = s
    while i < e:
        m = _RES_RE.match(d.lines[i])
        if m and i > s and d.keys[i - 1] not in ("resolution",):
            name = d.lines[i - 1]
            cam = CameraInfo(
                name=name,
                band=_band_of(name),
                width_px=_int(m.group(1)),
                height_px=_int(m.group(2)),
            )
            j = i + 1
            while j < e and j < i + 8:
                ln = d.lines[j]
                mm = _MM_RE.match(ln)
                px = _PIX_RE.match(ln)
                if mm:
                    cam.focal_mm = _num(mm.group(1))
                elif px and cam.pixel_um is None:
                    cam.pixel_um = _num(px.group(1))
                elif ln in ("Yes", "No"):
                    cam.precalibrated = ln == "Yes"
                    break
                j += 1
            cams.append(cam)
            i = j
        i += 1
    return cams


def _band_of(name: str) -> str | None:
    if "," in name:
        band = name.split(",", 1)[1].strip()
        band = re.sub(r"\s*\(.*$", "", band).rstrip("…. ")
        return band or None
    return None


def _norm_cam_name(name: str) -> str:
    return re.sub(r"[\s…]+", "", re.sub(r"\(.*$", "", name)).lower()


def _parse_calibrations(d: _Doc, rep: MetashapeReport) -> None:
    starts = [i for i, ln in enumerate(d.lines) if _RESID_RE.match(ln)]
    for n, i in enumerate(starts):
        name = _RESID_RE.match(d.lines[i]).group(1)  # type: ignore[union-attr]
        end = starts[n + 1] if n + 1 < len(starts) else len(d.lines)
        # 다음 큰 섹션이 먼저 오면 거기까지
        for t in (
            "Ground Control Points",
            "Camera Locations",
            "Digital Elevation Model",
            "Processing Parameters",
        ):
            j = d.find(t, i + 1)
            if 0 <= j < end:
                end = j
        cam = _match_camera(rep.cameras, name)
        if cam is None:
            cam = CameraInfo(name=name, band=_band_of(name))
            rep.cameras.append(cam)
        for j in range(i, min(end, i + 6)):
            m = _NIMG_RE.match(d.lines[j])
            if m:
                cam.n_images = _int(m.group(1))
                cam.precalibrated = "precalibrated" in m.group(2).lower() or cam.precalibrated
                break
        _parse_coef_table(d, i, end, cam, rep)


def _match_camera(cams: list[CameraInfo], name: str) -> CameraInfo | None:
    """보정 페이지의 카메라 이름을 카메라 표의 이름과 맞춘다.

    표의 이름은 칸이 좁으면 "RedEdge-M, Red edge (5…" 처럼 잘려 나온다. 괄호 이후를 뗀 정규화 키가
    같으면 일치, 잘린 이름("…")은 접두 일치까지 허용한다 ("Red" 가 "Red edge" 를 삼키지 않게 정확
    일치를 먼저 본다).
    """
    key = _norm_cam_name(name)
    for c in cams:
        if _norm_cam_name(c.name) == key:
            return c
    for c in cams:
        ck = _norm_cam_name(c.name)
        if "…" in c.name and key.startswith(ck):
            return c
    return None


def _parse_coef_table(d: _Doc, s: int, e: int, cam: CameraInfo, rep: MetashapeReport) -> None:
    """머리글(계수 이름이 두 번씩) → 행(이름×2, 값×2, [오차], [1.00, 상관…]).

    상관 행렬은 상삼각이고 열은 머리글 순서의 **조정된** 계수(오차가 있는 것) 만이다. 고정 계수(예:
    다중분광의 K3) 는 값만 있고 열에서도 빠진다.
    """
    i = d.find("Value", s, e)
    if i < 0:
        rep.warnings.append(f"{cam.name}: 보정 계수 표가 없음")
        return
    i += 1
    if i < e and d.keys[i] == "error":
        i += 1
    header: list[str] = []
    while i < e and _COEF_RE.match(d.lines[i].replace(" ", "")):
        name = d.lines[i].replace(" ", "")
        if header and name != header[-1] and name in header:
            break  # 머리글이 끝나고 첫 행의 이름이 다시 나온 것
        if not header or header[-1] != name:
            header.append(name)
        i += 1
    # 머리글의 마지막 이름이 첫 행 이름과 같으면(고정 K3) 중복 합치기로 한 줄이 됐다 → 행으로도 본다
    if (
        header
        and i < e
        and _is_number(d.lines[i])
        and d.lines[i - 1].replace(" ", "") == header[-1]
    ):
        i -= 1
    rows: list[tuple[str, list[str]]] = []
    while i < e:
        ln = d.lines[i].replace(" ", "")
        if ln.startswith("Table") or d.lines[i].startswith("Page "):
            break
        if _COEF_RE.match(ln):
            vals: list[str] = []
            j = i + 1
            while (
                j < e
                and not _COEF_RE.match(d.lines[j].replace(" ", ""))
                and not d.lines[j].startswith(("Table", "Page "))
            ):
                vals.append(d.lines[j])
                j += 1
            rows.append((ln, vals))
            i = j
        else:
            i += 1
    corr_rows: dict[str, list[float]] = {}
    for name, vals in rows:
        nums = [v for v in vals if _is_number(v)]
        if not nums:
            continue
        value = _num(nums[0])
        if value is None:
            continue
        rest = nums[2:] if len(nums) >= 2 and _num(nums[1]) == value else nums[1:]
        error = _num(rest[0]) if rest else None
        cam.coefficients[name] = CalibCoef(value=value, error=error)
        if error is not None and len(rest) > 1 and _num(rest[1]) == 1.0:
            corr_rows[name] = [c for c in (_num(x) for x in rest[2:]) if c is not None]
    cols = [h for h in header if h in cam.coefficients and cam.coefficients[h].error is not None]
    cam.correlations = {}
    for idx, name in enumerate(cols):
        corr = corr_rows.get(name, [])
        others = cols[idx + 1 :]
        if len(corr) != len(others):
            if corr:
                rep.warnings.append(
                    f"{cam.name}: {name} 상관 값 {len(corr)}개가 열 {len(others)}개와 맞지 않아 "
                    "건너뜀"
                )
            continue
        for other, cval in zip(others, corr, strict=True):
            cam.correlations[f"{name}-{other}"] = cval


def _parse_location_error(d: _Doc, s: int, e: int) -> LocationError | None:
    unit = "m"
    labels = []
    for i in range(s, e):
        m = re.match(r"^(X|Y|Z|XY|Total) error \((cm|m|mm)\)$", d.lines[i])
        if m:
            unit = m.group(2)
            labels.append((i, m.group(1)))
    if not labels:
        return None
    last = labels[-1][0]
    nums: list[float] = []
    for i in range(last + 1, e):
        if _is_number(d.lines[i]):
            v = _num(d.lines[i])
            if v is not None:
                nums.append(v)
        elif nums:
            break
    if len(nums) < 5:
        return None
    k = {"cm": 0.01, "mm": 0.001}.get(unit, 1.0)
    x, y, z, xy, total = [v * k for v in nums[:5]]
    return LocationError(unit=unit, x_m=x, y_m=y, z_m=z, xy_m=xy, total_m=total)


def _parse_gcp(d: _Doc, s: int, e: int) -> list[GcpSummary]:
    """'Control points RMSE' / 'Check points RMSE' 요약 행만 (Count, X, Y, Z, XY, Total)."""
    out: list[GcpSummary] = []
    unit = "m"
    for i in range(s, e):
        m = re.match(r"^(X|Y|Z|XY|Total) error \((cm|m|mm)\)$", d.lines[i])
        if m:
            unit = m.group(2)
    k = {"cm": 0.01, "mm": 0.001}.get(unit, 1.0)
    for i in range(s, e):
        m = re.match(r"^Table \d+\.\s*(Control|Check) points RMSE", d.lines[i])
        if not m:
            continue
        nums: list[float] = []
        for j in range(i - 1, max(s, i - 12), -1):
            if _is_number(d.lines[j]):
                v = _num(d.lines[j])
                if v is not None:
                    nums.insert(0, v)
            elif nums:
                break
        if len(nums) >= 6:
            cnt, x, y, z, xy, total = nums[-6:]
            out.append(
                GcpSummary(
                    kind=m.group(1).lower(),
                    count=int(cnt),
                    x_m=x * k,
                    y_m=y * k,
                    z_m=z * k,
                    xy_m=xy * k,
                    total_m=total * k,
                )
            )
    return out


def load_report(path: Path) -> MetashapeReport:
    lines, n_pages = extract_lines(path)
    rep = parse_lines(lines, source=path.name, n_pages=n_pages)
    if rep.n_images is None and not rep.cameras:
        raise MetashapeError(f"보고서에서 수치를 찾지 못함: {path.name}")
    return rep


def extract_figures(path: Path) -> list[tuple[str, bytes]]:
    """PDF 의 JPEG 그림을 (캡션, 바이트) 로. 첫 쪽(표지 로고)과 작은 색상 막대는 뺀다.

    Metashape 보고서 그림은 전부 DCTDecode(JPEG) XObject 라 Pillow 없이 바이트를 그대로 쓸 수 있다
    (실측: 두 샘플 모두). 다른 필터는 건너뛴다.
    """
    from pypdf import PdfReader

    out: list[tuple[str, bytes]] = []
    try:
        reader = PdfReader(str(path))
        for pi, page in enumerate(reader.pages):
            if pi == 0:
                continue
            text = page.extract_text() or ""
            caps = [m.group(1) for ln in text.splitlines() if (m := _FIG_RE.match(ln.strip()))]
            res = page.get("/Resources") or {}
            xobjs = res.get("/XObject") or {}
            k = 0
            for name in list(xobjs.keys()):
                obj = xobjs[name].get_object()
                if obj.get("/Subtype") != "/Image" or obj.get("/Filter") != "/DCTDecode":
                    continue
                data = obj.get_data()
                if len(data) < 2_000:
                    continue
                cap = caps[k] if k < len(caps) else f"{pi + 1}쪽 그림"
                out.append((cap, bytes(data)))
                k += 1
    except Exception as e:  # noqa: BLE001 - 그림은 보조 정보
        log.warning("보고서 그림 추출 실패 %s: %s", path.name, e)
    return out


# --------------------------------------------------------------------------- 판정

RTK_HINTS = ("m3m", "mavic", "zenmuse", "p1", "l1", "l2", "phantom 4 rtk", "p4r")
GPS_HINTS = ("rededge", "altum", "micasense", "sequoia")


def location_profile(rep: MetashapeReport, cfg: MetashapeConfig) -> str:
    if cfg.location_profile in ("rtk", "gps"):
        return cfg.location_profile
    names = " ".join(c.name for c in rep.cameras).lower()
    if any(h in names for h in GPS_HINTS):
        return "gps"
    if any(h in names for h in RTK_HINTS):
        return "rtk"
    return "gps"


def judge(
    rep: MetashapeReport, cfg: MetashapeConfig, flight: RunReport | None = None
) -> MetashapeResult:
    groups = [
        _group_alignment(rep, cfg),
        _group_calibration(rep, cfg),
        _group_location(rep, cfg),
        _group_products(rep),
    ]
    status = flight_status(groups, cfg.warn_to_fail_count)
    if flight is not None:
        # 대조 그룹은 진단용: 다른 비행이 짝지어졌거나 기준면이 달라도 "재처리" 는 아니므로
        # WARN 까지만 전체 판정에 반영하고 WARN 누적 FAIL 에도 넣지 않는다.
        cross = _group_crosscheck(rep, flight, cfg)
        groups.append(cross)
        cross_worst = worst([m.status for m in cross.metrics])
        if cross_worst in (Status.WARN, Status.FAIL):
            status = worst([status, Status.WARN])
    metrics = [m for g in groups for m in g.metrics]
    n_fail = sum(1 for m in metrics if m.status is Status.FAIL)
    n_warn = sum(1 for m in metrics if m.status is Status.WARN)
    gates = sorted({m.label for m in metrics if m.status is Status.FAIL and m.gate})
    if status is Status.FAIL:
        headline = "재처리 검토 필요"
        detail = (
            f"게이트 항목 실패 {len(gates)}건: {', '.join(gates)}"
            if gates
            else f"실패 {n_fail}건 · 경고 {n_warn}건"
        )
    elif status is Status.WARN:
        headline = "조건부 사용 가능"
        detail = f"경고 {n_warn}건 · 아래 권고 조치 확인"
    elif status is Status.PASS:
        headline = "후처리 결과 양호"
        detail = "모든 항목 기준 충족"
    else:
        headline = "판정 불가"
        detail = "보고서에서 판정할 수치를 찾지 못함"
    actions = [
        m.next_action for m in metrics if m.next_action and m.status in (Status.WARN, Status.FAIL)
    ]
    return MetashapeResult(
        generated_at=datetime.now().astimezone(),
        report=rep,
        groups=groups,
        status=status,
        headline=headline,
        headline_detail=detail,
        next_actions=actions,
        flight_root=str(flight.root) if flight is not None else None,
    )


def _group_alignment(rep: MetashapeReport, cfg: MetashapeConfig) -> CheckGroup:
    ms: list[Metric] = []
    pct = rep.aligned_pct
    st = grade_min(pct, cfg.aligned_warn_pct, cfg.aligned_fail_pct)
    missing = (rep.n_images or 0) - (rep.n_aligned or 0)
    ms.append(
        Metric(
            key="ms.aligned",
            label="영상 정렬 성공률",
            value=round(pct, 1) if pct is not None else None,
            unit="%",
            threshold=f"WARN < {cfg.aligned_warn_pct:.0f}%, FAIL < {cfg.aligned_fail_pct:.0f}%",
            status=st,
            reason=(
                f"{rep.n_aligned:,} / {rep.n_images:,} 장 정렬"
                + (f", 미정렬 {missing:,}장" if missing > 0 else "")
                + (
                    f" (캡처 {rep.n_images // rep.n_bands:,} × {rep.n_bands} 밴드)"
                    if rep.is_multispectral and rep.n_images
                    else ""
                )
                if pct is not None
                else "영상 수 또는 정렬 수를 찾지 못함"
            ),
            next_action=(
                "미정렬 카메라 확인 후 Align Selected Cameras 로 재정렬 · "
                "안 되면 해당 구간 재비행\n"
                "영향: 미정렬 영상은 정사영상 공백·왜곡"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            gate=True,
        )
    )
    rms = (
        rep.rms_reprojection_px
        if rep.rms_reprojection_px is not None
        else rep.reprojection_error_px
    )
    st = grade_max(rms, cfg.reproj_rms_warn_px, cfg.reproj_rms_fail_px)
    ms.append(
        Metric(
            key="ms.reproj_rms",
            label="재투영 오차 RMS",
            value=round(rms, 3) if rms is not None else None,
            unit="px",
            threshold=f"WARN > {cfg.reproj_rms_warn_px:g} px, FAIL > {cfg.reproj_rms_fail_px:g} px",
            status=st,
            reason=(
                f"정규화 {fmt(rep.rms_reprojection_norm, 3)} · 타이포인트 {rep.tie_points:,} "
                f"(전체 {rep.tie_points_total:,}) · 투영 {rep.projections:,}"
                if rms is not None and rep.tie_points and rep.tie_points_total and rep.projections
                else (f"{fmt(rms, 3, 'px')}" if rms is not None else "값 없음")
            ),
            next_action=(
                "Gradual Selection(Reprojection Error)으로 오차 큰 타이포인트 제거 후 "
                "Optimize Cameras 재실행\n영향: 재투영 오차가 크면 정합 정밀도 저하"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    mx = rep.max_reprojection_px
    st = grade_max(mx, cfg.reproj_max_warn_px, float("inf"))
    ms.append(
        Metric(
            key="ms.reproj_max",
            label="재투영 오차 최대",
            value=round(mx, 1) if mx is not None else None,
            unit="px",
            threshold=f"WARN > {cfg.reproj_max_warn_px:g} px",
            status=st,
            reason=(
                f"RMS 의 {mx / rms:.0f}배"
                if mx is not None and rms
                else ("값 없음" if mx is None else "")
            ),
            next_action=(
                "Gradual Selection 으로 상위 10 % 제거 후 Optimize Cameras\n"
                "원인: 극단 타이포인트 잔존"
                if st is Status.WARN
                else None
            ),
        )
    )
    mult = rep.tie_point_multiplicity
    st = grade_min(mult, cfg.multiplicity_warn, 0.0)
    ms.append(
        Metric(
            key="ms.multiplicity",
            label="타이포인트 평균 관측 영상 수",
            value=round(mult, 2) if mult is not None else None,
            unit="장",
            threshold=f"WARN < {cfg.multiplicity_warn:g}",
            status=st,
            reason=(
                f"타이포인트 하나가 평균 {mult:.2f}장에서 관측됨 · 평균 키포인트 크기 "
                f"{fmt(rep.mean_keypoint_size_px, 2, 'px')}"
                if mult is not None
                else "값 없음"
            ),
            next_action=(
                "중복도·비행고도 점검\n원인: 중복 부족 또는 약한 텍스처"
                if st is Status.WARN
                else None
            ),
        )
    )
    acc = rep.alignment.get("Accuracy")
    low = acc is not None and acc.lower() in ("low", "lowest")
    ms.append(
        Metric(
            key="ms.alignment_accuracy",
            label="정렬 정확도 설정",
            value=acc,
            status=Status.WARN if low else (Status.PASS if acc else Status.NA),
            threshold="WARN: Low 이하",
            reason=(
                f"키포인트 한도 {rep.alignment.get('Key point limit', '—')}, "
                f"타이포인트 한도 {rep.alignment.get('Tie point limit', '—')}, "
                f"적응형 카메라 모델 {rep.alignment.get('Adaptive camera model fitting', '—')}"
                if rep.alignment
                else "정렬 파라미터 없음"
            ),
            next_action="정확도 High 로 재정렬 권장" if low else None,
        )
    )
    return CheckGroup(key="ms_align", label="정렬·타이포인트", metrics=ms)


def _group_calibration(rep: MetashapeReport, cfg: MetashapeConfig) -> CheckGroup:
    ms: list[Metric] = []
    if not rep.cameras:
        ms.append(
            Metric(key="ms.calib", label="카메라 보정", status=Status.NA, reason="카메라 표 없음")
        )
        return CheckGroup(key="ms_calib", label="카메라 보정", metrics=ms)
    worst_corr: tuple[str, str, float] | None = None
    for c in rep.cameras:
        mc = c.max_abs_correlation
        if mc and (worst_corr is None or abs(mc[1]) > abs(worst_corr[2])):
            worst_corr = (c.name, mc[0], mc[1])
    corr_val = abs(worst_corr[2]) if worst_corr else None
    st = grade_max(corr_val, cfg.calib_corr_warn, 1.01)
    ms.append(
        Metric(
            key="ms.calib_corr",
            label="보정 계수 간 최대 상관계수",
            value=round(corr_val, 2) if corr_val is not None else None,
            threshold=f"WARN ≥ {cfg.calib_corr_warn:g} (절댓값)",
            status=st,
            reason=(
                f"{worst_corr[0]} 의 {worst_corr[1]} = {worst_corr[2]:+.2f}"
                + (
                    "; 카메라 "
                    + ", ".join(
                        f"{c.band or c.name}: {c.max_abs_correlation[0]} "
                        f"{c.max_abs_correlation[1]:+.2f}"
                        for c in rep.cameras
                        if c.max_abs_correlation
                    )
                    if len(rep.cameras) > 1
                    else ""
                )
                if worst_corr
                else "상관 행렬 없음"
            ),
            next_action=(
                "사전보정 값 고정(Fixed parameters) 또는 K3·P 계수 제외 후 Optimize Cameras\n"
                "원인: 계수 간 높은 상관 = 보정 모델 과매개(서로 상쇄) 가능성"
                if st is Status.WARN
                else None
            ),
        )
    )
    pre = [c.precalibrated for c in rep.cameras]
    all_pre = all(p is True for p in pre)
    ms.append(
        Metric(
            key="ms.precalibrated",
            label="사전보정(Precalibrated) 사용",
            value="예" if all_pre else ("일부" if any(p is True for p in pre) else "아니오"),
            status=Status.PASS if all_pre else Status.NA,
            reason=", ".join(
                f"{c.band or c.name}: {'예' if c.precalibrated else '아니오'}"
                + (f" ({c.n_images:,}장)" if c.n_images else "")
                for c in rep.cameras
            ),
        )
    )
    f_rel: list[float] = []
    for c in rep.cameras:
        f = c.coefficients.get("F")
        if f and f.error is not None and f.value:
            f_rel.append(f.error / f.value * 100.0)
    ms.append(
        info_metric(
            "ms.focal_error",
            "초점거리(F) 상대 오차 (최대)",
            round(max(f_rel), 4) if f_rel else None,
            "%",
            "; ".join(
                f"{c.band or c.name}: F {c.coefficients['F'].value:,.2f} "
                f"± {c.coefficients['F'].error}"
                for c in rep.cameras
                if "F" in c.coefficients and c.coefficients["F"].error is not None
            )
            or "F 오차 없음",
        )
    )
    ms.append(
        info_metric(
            "ms.cameras",
            "카메라 수 (밴드)",
            len(rep.cameras),
            "대",
            ", ".join(
                f"{c.name} {c.width_px}×{c.height_px} {fmt(c.focal_mm, 2, 'mm')}"
                for c in rep.cameras
            ),
        )
    )
    return CheckGroup(key="ms_calib", label="카메라 보정", metrics=ms)


def _group_location(rep: MetashapeReport, cfg: MetashapeConfig) -> CheckGroup:
    ms: list[Metric] = []
    prof = location_profile(rep, cfg)
    le = rep.location_error
    if prof == "rtk":
        xy_w, xy_f, z_w, z_f = (
            cfg.rtk_xy_warn_m,
            cfg.rtk_xy_fail_m,
            cfg.rtk_z_warn_m,
            cfg.rtk_z_fail_m,
        )
    else:
        xy_w, xy_f, z_w, z_f = (
            cfg.gps_xy_warn_m,
            cfg.gps_xy_fail_m,
            cfg.gps_z_warn_m,
            cfg.gps_z_fail_m,
        )
    prof_text = "RTK 기체 (cm 급)" if prof == "rtk" else "카메라 GPS (m 급, Quick Mount RedEdge 등)"
    xy = le.xy_m if le else None
    st = grade_max(xy, xy_w, xy_f)
    ms.append(
        Metric(
            key="ms.loc_xy",
            label="카메라 위치 오차 XY",
            value=round(xy, 3) if xy is not None else None,
            unit="m",
            threshold=f"{prof_text}: WARN > {xy_w:g} m, FAIL > {xy_f:g} m",
            status=st,
            reason=(
                f"X {le.x_m:.3f} · Y {le.y_m:.3f} m (보고서 단위 {le.unit}); GSD 의 "
                f"{xy / (rep.gsd_cm / 100.0):.1f}배"
                if le
                and xy is not None
                and le.x_m is not None
                and le.y_m is not None
                and rep.gsd_cm
                else "카메라 위치 오차 표 없음"
            ),
            next_action=(
                "RTK 고정해 여부 확인 · PPK 후처리 또는 GCP 투입 검토\n"
                "원인: 위치 오차가 기체 측위 정확도 초과"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    z = le.z_m if le else None
    st = grade_max(z, z_w, z_f)
    ms.append(
        Metric(
            key="ms.loc_z",
            label="카메라 위치 오차 Z",
            value=round(z, 3) if z is not None else None,
            unit="m",
            threshold=f"{prof_text}: WARN > {z_w:g} m, FAIL > {z_f:g} m",
            status=st,
            reason=(f"총오차 {le.total_m:.3f} m" if le and le.total_m is not None else "값 없음"),
            next_action=(
                "GCP 또는 PPK 로 보정\n영향: 고도 오차가 크면 DEM 절대고도 편차"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
        )
    )
    if rep.gcp:
        for g in rep.gcp:
            lim_xy = cfg.gcp_xy_gsd_mult * (rep.gsd_cm or 0) / 100.0
            lim_z = cfg.gcp_z_gsd_mult * (rep.gsd_cm or 0) / 100.0
            st_xy = grade_max(g.xy_m, lim_xy, lim_xy * 2) if rep.gsd_cm else Status.NA
            st_z = grade_max(g.z_m, lim_z, lim_z * 2) if rep.gsd_cm else Status.NA
            st = worst([st_xy, st_z])
            ms.append(
                Metric(
                    key=f"ms.gcp_{g.kind}",
                    label="GCP RMSE" if g.kind == "control" else "검사점(Check point) RMSE",
                    value=round(g.total_m, 3) if g.total_m is not None else None,
                    unit="m",
                    threshold=f"XY ≤ {cfg.gcp_xy_gsd_mult:g} GSD, Z ≤ {cfg.gcp_z_gsd_mult:g} GSD",
                    status=st,
                    reason=f"{g.count}점 · XY {fmt(g.xy_m, 3, 'm')} · Z {fmt(g.z_m, 3, 'm')}",
                    next_action=(
                        "GCP 측정값·마커 배치 재확인 후 Optimize Cameras"
                        if st is not Status.PASS
                        else None
                    ),
                    gate=g.kind == "control",
                )
            )
    else:
        ms.append(
            Metric(
                key="ms.gcp_control",
                label="GCP RMSE",
                status=Status.NA,
                reason="보고서에 GCP 표 없음 (GCP 미사용 프로젝트)",
            )
        )
    return CheckGroup(key="ms_loc", label="위치 정확도", metrics=ms)


def _group_products(rep: MetashapeReport) -> CheckGroup:
    ms: list[Metric] = []
    ms.append(
        info_metric(
            "ms.gsd",
            "지상해상도 (GSD)",
            rep.gsd_cm,
            "cm/px",
            f"비행고도 {fmt(rep.flying_altitude_m, 1, 'm')} · "
            f"면적 {fmt(rep.coverage_km2, 3, 'km²')}"
            + (f" ({rep.coverage_km2 * 100:.1f} ha)" if rep.coverage_km2 else ""),
        )
    )
    dq = rep.depth_maps.get("Quality")
    low = dq is not None and dq.lower() in ("low", "lowest")
    ms.append(
        Metric(
            key="ms.depth_quality",
            label="깊이맵 품질 설정",
            value=dq,
            status=Status.WARN if low else (Status.PASS if dq else Status.NA),
            threshold="WARN: Low 이하",
            reason=(
                f"필터 {rep.depth_maps.get('Filtering mode', '—')} · 깊이맵 "
                f"{rep.depth_maps.get('Count', '—')}개"
                if rep.depth_maps
                else "깊이맵 파라미터 없음"
            ),
            next_action="Medium 이상으로 재생성 권장" if low else None,
        )
    )
    dem_res = _num(rep.dem.get("resolution") or rep.dem.get("Resolution"))
    ms.append(
        info_metric(
            "ms.dem",
            "DEM 해상도",
            dem_res,
            "cm/px",
            f"점 밀도 {rep.dem.get('point_density', '—')} · 크기 {rep.dem.get('Size', '—')} px",
        )
    )
    o = rep.orthomosaic
    ms.append(
        info_metric(
            "ms.ortho",
            "정사영상",
            _num(o.get("Resolution")),
            "cm/px",
            f"크기 {o.get('Size', '—')} px · {o.get('Colors', '—')} · 블렌딩 "
            f"{o.get('Blending mode', '—')} · "
            f"좌표계 {o.get('Coordinate system', rep.coordinate_system or '—')}"
            if o
            else "정사영상 정보 없음",
        )
    )
    if rep.is_multispectral:
        has = bool(rep.raster_transform)
        ms.append(
            Metric(
                key="ms.reflectance",
                label="반사율 변환식 (Raster Transform)",
                value="있음" if has else "없음",
                status=Status.PASS if has else Status.WARN,
                threshold="다중분광은 반사율 변환식이 있어야 식생지수 계산 가능",
                reason=rep.raster_transform or "보고서에 변환식 없음",
                next_action=(
                    "Calibrate Reflectance 수행 · Raster Transform 에 반사율 식 설정 후 "
                    "정사영상 재내보내기"
                    if not has
                    else None
                ),
            )
        )
    ms.append(
        info_metric(
            "ms.software",
            "Metashape 버전",
            rep.software_version,
            None,
            f"{rep.system.get('Software name', '—')} · "
            f"{rep.system.get('OS', '—')} · GPU {rep.system.get('GPU(s)', '—')}",
        )
    )
    return CheckGroup(key="ms_products", label="산출물·설정", metrics=ms)


def _group_crosscheck(rep: MetashapeReport, flight: RunReport, cfg: MetashapeConfig) -> CheckGroup:
    """같은 현장의 FlightQA 분석과 대조."""
    ms: list[Metric] = []
    n_captures = flight.n_captures
    n_bands = rep.n_bands
    expected = n_captures * n_bands
    st = Status.NA
    if rep.n_images is not None and expected:
        diff_pct = abs(rep.n_images - expected) / expected * 100.0
        st = grade_max(diff_pct, cfg.cross_images_warn_pct, cfg.cross_images_fail_pct)
    ms.append(
        Metric(
            key="ms.x_images",
            label="영상 수 대조 (캡처 × 밴드)",
            value=rep.n_images,
            unit="장",
            threshold=(
                f"기대 {expected:,}장 (캡처 {n_captures:,} × {n_bands} 밴드) ; "
                f"WARN 차이 > {cfg.cross_images_warn_pct:g}%"
            ),
            status=st,
            reason=(
                f"보고서 {rep.n_images:,}장 vs 비행 분석 {expected:,}장 "
                f"({rep.n_images - expected:+,})"
                if rep.n_images is not None
                else "값 없음"
            ),
            next_action=(
                "누락 폴더(SET·패널 제외) 또는 중복 투입 확인\n"
                "원인: Metashape 투입 영상 수가 촬영 캡처 수와 다름"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            gate=False,
        )
    )
    agl = [f.summary.mean_agl_m for f in flight.flights if f.summary.mean_agl_m]
    agl_mean = sum(agl) / len(agl) if agl else None
    st, dev = _dev(rep.flying_altitude_m, agl_mean, cfg.cross_dev_warn_pct)
    ms.append(
        Metric(
            key="ms.x_altitude",
            label="비행고도 대조",
            value=rep.flying_altitude_m,
            unit="m",
            threshold=f"비행 분석 평균 AGL 대비 ± {cfg.cross_dev_warn_pct:g}%",
            status=st,
            reason=(
                f"보고서 {fmt(rep.flying_altitude_m, 1, 'm')} vs 평균 AGL "
                f"{fmt(agl_mean, 1, 'm')} (편차 {fmt(dev, 0, '%')})"
                + (
                    "; Metashape 고도는 재구성 표면(수관) 기준이라 산림지는 이륙점 기준 AGL 보다 "
                    "낮게 나오는 것이 정상"
                    if dev is not None
                    and agl_mean is not None
                    and rep.flying_altitude_m is not None
                    and rep.flying_altitude_m < agl_mean
                    else ""
                )
                if dev is not None
                else "대조할 AGL 없음 (지형추종 또는 고도 미상)"
            ),
            next_action=(
                "짝지은 비행 분석 확인\n"
                "원인: 수관 높이로 설명되지 않는 차이는 다른 비행 또는 다른 기준면(타원체·지오이드)"
                if st is Status.WARN
                else None
            ),
        )
    )
    gsd_vals = [
        m.value
        for f in flight.flights
        for g in f.groups
        for m in g.metrics
        if m.key == "overlap.gsd" and isinstance(m.value, int | float)
    ]
    gsd_flight = float(gsd_vals[0]) if gsd_vals else None
    st, dev = _dev(rep.gsd_cm, gsd_flight, cfg.cross_dev_warn_pct)
    ms.append(
        Metric(
            key="ms.x_gsd",
            label="GSD 대조",
            value=rep.gsd_cm,
            unit="cm/px",
            threshold=f"비행 분석 GSD 중앙값 대비 ± {cfg.cross_dev_warn_pct:g}%",
            status=st,
            reason=(
                f"보고서 {fmt(rep.gsd_cm, 2, 'cm/px')} vs 비행 분석 "
                f"{fmt(gsd_flight, 2, 'cm/px')} (편차 {fmt(dev, 0, '%')})"
                if dev is not None
                else "대조할 GSD 없음"
            ),
        )
    )
    # Metashape 의 Coverage area 는 영상 자취의 합집합이므로 FlightQA 의 커버리지 합집합과 비교한다
    # (조사 구역 추정치는 여유 폭 0 의 궤적 외곽이라 훨씬 작다). 커버리지가 없으면 조사 구역으로.
    cov = [f.coverage.area_m2 for f in flight.flights if f.coverage]
    areas = [] if cov else [f.survey_area.area_m2 for f in flight.flights if f.survey_area]
    ref = (sum(cov) if cov else sum(areas)) if (cov or areas) else None
    rep_m2 = rep.coverage_km2 * 1e6 if rep.coverage_km2 else None
    st, dev = _dev(rep_m2, ref, cfg.cross_area_warn_pct)
    ms.append(
        Metric(
            key="ms.x_area",
            label="면적 대조",
            value=round(rep_m2 / 1e4, 2) if rep_m2 else None,
            unit="ha",
            threshold=(
                f"비행 분석 {'커버리지 합집합' if cov else '추정 조사 구역'} 대비 "
                f"± {cfg.cross_area_warn_pct:g}%"
            ),
            status=st,
            reason=(
                f"보고서 {rep_m2 / 1e4:.2f} ha vs 비행 분석 {ref / 1e4:.2f} ha "
                f"(편차 {fmt(dev, 0, '%')})"
                if dev is not None and rep_m2 and ref
                else "대조할 면적 없음"
            ),
        )
    )
    sensor_bands = {"rededge_mx": 5, "dji_m3m": 4}.get(flight.sensor.value if flight.sensor else "")
    st = Status.NA
    if sensor_bands is not None and rep.cameras:
        st = Status.PASS if len(rep.cameras) in (sensor_bands, 1) else Status.WARN
    ms.append(
        Metric(
            key="ms.x_bands",
            label="밴드 수 대조",
            value=len(rep.cameras),
            unit="대",
            threshold=(
                f"센서 {flight.sensor_display or '—'}: 다중분광 {sensor_bands or '—'} 밴드 "
                "또는 RGB 1"
            ),
            status=st,
            reason=", ".join(c.band or c.name for c in rep.cameras) or "카메라 없음",
            next_action=(
                "카메라 그룹(Multi-camera system) 설정 확인\n원인: 밴드 일부가 프로젝트에서 누락"
                if st is Status.WARN
                else None
            ),
        )
    )
    return CheckGroup(key="ms_cross", label=f"비행 분석과 대조 ({flight.root.name})", metrics=ms)


def _dev(a: float | None, b: float | None, warn_pct: float) -> tuple[Status, float | None]:
    if a is None or b is None or b == 0:
        return Status.NA, None
    dev = abs(a - b) / abs(b) * 100.0
    return (Status.WARN if dev > warn_pct else Status.PASS), round(dev, 1)


# --------------------------------------------------------------------------- 내보내기


def to_rows(result: MetashapeResult) -> list[dict[str, Any]]:
    """항목별 CSV 행."""
    return [
        {
            "group": g.label,
            "key": m.key,
            "label": m.label,
            "value": m.value,
            "unit": m.unit or "",
            "status": m.status.value,
            "threshold": m.threshold or "",
            "reason": m.reason,
            "next_action": m.next_action or "",
        }
        for g in result.groups
        for m in g.metrics
    ]
