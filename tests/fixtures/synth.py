"""합성 테스트 데이터 생성기.

실제 현장 데이터는 저장소에 넣지 않는다 (CLAUDE.md §8, §12). 대신 실제 샘플과 같은
EXIF/XMP 구조(docs/tags_*_numeric.json)를 가진 소형 TIFF/JPG 를 만들고 exiftool 로 태그를 쓴다.

- 기하: 동서 방향 왕복 라인, 라인 사이 전환 캡처 2 개. 좌표는 가상 원점(37.0N, 127.0E) 근처.
- 이상 주입: 밴드 누락, 시간 공백, 중복, 손상 파일, 고도 이탈, 연직 이탈, RTK 미고정, 포화 픽셀.
- 태그 쓰기는 exiftool 프로세스 1 개 + argfile (``-execute`` 구분) 로 한다.
"""

from __future__ import annotations

import math
import random
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import tifffile

FIXTURES = Path(__file__).resolve().parent
EXIFTOOL_CONFIG = FIXTURES / "exiftool_synth.config"

# 두 센서 모두 12-bit 값을 4비트 좌시프트한 uint16 으로 저장한다 (docs/ASSUMPTIONS.md).
SATURATION_DN = 4095 << 4  # 65520
M_PER_DEG_LAT = 111_320.0

REDEDGE_BANDS = [  # (파일 접미사, BandName, 중심파장, RigCameraIndex)
    ("1", "Blue", 475, 0),
    ("2", "Green", 560, 1),
    ("3", "Red", 668, 2),
    ("4", "NIR", 842, 3),
    ("5", "Red edge", 717, 4),
]
REDEDGE_KEYS = {"1": "blue", "2": "green", "3": "red", "4": "nir", "5": "rededge"}
M3M_BANDS = [  # (접미사, BandName, 중심파장, RigCameraIndex, ImageSource)
    ("G", "Green", 560, 1, "MS_G_CAMERA"),
    ("R", "Red", 650, 2, "MS_R_CAMERA"),
    ("RE", "RedEdge", 730, 3, "MS_RE_CAMERA"),
    ("NIR", "NIR", 860, 4, "MS_NIR_CAMERA"),
]
M3M_KEYS = {"G": "green", "R": "red", "RE": "rededge", "NIR": "nir", "D": "rgb"}


def make_tiff(
    path: Path,
    *,
    width: int = 1280,
    height: int = 960,
    fill: int = 20000,
    saturated_fraction: float = 0.0,
    seed: int = 0,
    texture: float = 0.3,
    panel_rect: bool = False,
    flat: bool = False,
) -> Path:
    """단일 밴드 16-bit TIFF.

    - ``texture``: fill 대비 ±비율의 균일 잡음 (실제 영상처럼 다이내믹 레인지를 쓰게).
      ``flat=True`` 면 상수.
    - ``saturated_fraction``: 그 비율의 픽셀을 포화 DN 으로.
    - ``panel_rect``: 중앙에 밝고 균일한 사각형(캘리브레이션 패널 흉내, 면적 ~9%).
    """
    rng = np.random.default_rng(seed)
    if flat or texture <= 0:
        img = np.full((height, width), fill, dtype=np.uint16)
    else:
        lo, hi = int(fill * (1 - texture)), int(fill * (1 + texture))
        img = rng.integers(lo, hi + 1, size=(height, width), dtype=np.int64).astype(np.uint16)
    if panel_rect:
        h0, h1 = int(height * 0.35), int(height * 0.65)
        w0, w1 = int(width * 0.35), int(width * 0.65)
        img[h0:h1, w0:w1] = (
            min(int(fill * 2.2), SATURATION_DN - 2000)
            + rng.integers(-50, 50, size=(h1 - h0, w1 - w0))
        ).astype(np.uint16)
    n_sat = round(saturated_fraction * img.size)
    if n_sat:
        idx = rng.choice(img.size, size=n_sat, replace=False)
        img.flat[idx] = SATURATION_DN
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, img, photometric="minisblack")
    return path


def make_jpeg(path: Path, *, width: int = 64, height: int = 48) -> Path:
    """소형 RGB JPEG (PySide6 QImage 로 인코딩; 화면 없이 동작)."""
    from PySide6.QtGui import QImage

    img = QImage(width, height, QImage.Format.Format_RGB888)
    img.fill(0x808080)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(path)):
        raise RuntimeError(f"JPEG 저장 실패: {path}")
    return path


@dataclass
class CaptureTruth:
    index: int
    capture_id: str
    time_utc: datetime
    lat: float
    lon: float
    alt_msl_m: float
    agl_m: float
    line: int
    is_turn: bool
    bands: list[str]
    files: list[Path] = field(default_factory=list)


@dataclass
class FlightSpec:
    sensor: str = "rededge_mx"
    n_lines: int = 3
    captures_per_line: int = 10
    line_spacing_m: float = 30.0
    interval_s: float = 3.8
    speed_mps: float = 5.0
    agl_m: float = 120.0
    takeoff_alt_msl_m: float = 100.0
    start_utc: datetime = field(default_factory=lambda: datetime(2026, 6, 1, 1, 0, 0, tzinfo=UTC))
    origin_lat: float = 37.0
    origin_lon: float = 127.0
    utc_offset_min: int = 540
    seed: int = 1
    image_size: tuple[int, int] = (64, 48)
    turn_captures: int = 2
    ground_captures: int = 0
    """비행 전 지상(AGL≈0) 캡처 수 — 패널 촬영 흉내."""
    ground_captures_post: int = 0
    """비행 후 지상 캡처 수."""
    # 이상 주입 (캡처 인덱스 기준)
    drop_bands: dict[int, list[str]] = field(default_factory=dict)
    gap_after: dict[int, float] = field(default_factory=dict)
    duplicate_captures: list[int] = field(default_factory=list)
    corrupt_files: list[int] = field(default_factory=list)
    zero_byte_files: list[int] = field(default_factory=list)
    agl_offset: dict[int, float] = field(default_factory=dict)
    off_nadir_deg: dict[int, float] = field(default_factory=dict)
    rtk_flag: dict[int, int] = field(default_factory=dict)
    irradiance_scale: dict[int, float] = field(default_factory=dict)
    saturated_fraction: dict[int, float] = field(default_factory=dict)
    odd_name_captures: list[int] = field(default_factory=list)
    """RedEdge: 파일명을 규칙에 안 맞게(IMXXX_X.TIF) 바꿔 8.3 이름 같은 불일치를 흉내.

    NTFS 단축명 충돌을 피하려고 ``~`` 는 쓰지 않는다.
    """
    second_flight_gap_min: float | None = None
    panel_on_ground: bool = True
    """지상 캡처 영상에 패널 사각형을 그린다."""
    solar_elevation_offset_deg: float = 0.0
    """RedEdge XMP SolarElevation 에 더할 오차 (교차검증 테스트용)."""


@dataclass
class BuildResult:
    root: Path
    spec: FlightSpec
    truth: list[CaptureTruth]
    files: list[Path]

    @property
    def n_captures(self) -> int:
        return len(self.truth)


def _positions(spec: FlightSpec) -> list[tuple[float, float, int, bool]]:
    """(x_m, y_m, line, is_turn) 목록. 동서 왕복, 라인마다 방향 반전."""
    step = spec.speed_mps * spec.interval_s
    pts: list[tuple[float, float, int, bool]] = []
    for ln in range(spec.n_lines):
        y = ln * spec.line_spacing_m
        xs = [i * step for i in range(spec.captures_per_line)]
        if ln % 2 == 1:
            xs = xs[::-1]
        pts.extend((x, y, ln, False) for x in xs)
        if ln < spec.n_lines - 1:
            x_end = xs[-1]
            for k in range(1, spec.turn_captures + 1):
                frac = k / (spec.turn_captures + 1)
                dx = (step if ln % 2 == 0 else -step) * frac * 1.5
                pts.append((x_end + dx, y + spec.line_spacing_m * frac, ln, True))
    return pts


def _latlon(spec: FlightSpec, x: float, y: float) -> tuple[float, float]:
    lat = spec.origin_lat + y / M_PER_DEG_LAT
    lon = spec.origin_lon + x / (M_PER_DEG_LAT * math.cos(math.radians(spec.origin_lat)))
    return lat, lon


def _token(rng: random.Random, n: int = 20) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _solar_rad(t: datetime, lat: float, lon: float) -> tuple[float, float]:
    """pvlib 겉보기 태양고도·방위각 (라디안). RedEdge DLS 태그 형식."""
    import pandas as pd
    from pvlib import solarposition

    pos = solarposition.get_solarposition(pd.DatetimeIndex([t]), lat, lon)
    return (
        math.radians(float(pos["apparent_elevation"].iloc[0])),
        math.radians(float(pos["azimuth"].iloc[0])),
    )


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def build_dataset(root: Path, spec: FlightSpec, exiftool: Path) -> BuildResult:
    rng = random.Random(spec.seed)
    root.mkdir(parents=True, exist_ok=True)
    truth: list[CaptureTruth] = []
    files: list[Path] = []
    commands: list[list[str]] = []  # exiftool 명령 (argfile 항목)
    utime_plan: list[tuple[Path, float]] = []

    def one_flight(start: datetime, flight_no: int, index_offset: int) -> int:
        t = start
        pts = _positions(spec)
        ground = [(0.0, -10.0, 0, True)] * spec.ground_captures
        x_end = pts[-1][0] if pts else 0.0
        post = [(x_end, -10.0, max(0, spec.n_lines - 1), True)] * spec.ground_captures_post
        all_pts = ground + pts + post
        idx = index_offset
        for k, (x, y, ln, is_turn) in enumerate(all_pts):
            if k > 0:
                t = t + timedelta(seconds=spec.interval_s + spec.gap_after.get(idx - 1, 0.0))
            lat, lon = _latlon(spec, x, y)
            on_ground = k < spec.ground_captures or k >= spec.ground_captures + len(pts)
            agl = 0.0 if on_ground else spec.agl_m + spec.agl_offset.get(idx, 0.0)
            alt = spec.takeoff_alt_msl_m + agl
            tilt = spec.off_nadir_deg.get(idx, 0.0)
            irr_scale = spec.irradiance_scale.get(idx, 1.0)
            heading = 90.0 if ln % 2 == 0 else 270.0
            n_copies = 2 if idx in spec.duplicate_captures else 1
            if spec.sensor == "rededge_mx":
                cid = _token(rng)
                bands = [
                    b
                    for b in REDEDGE_BANDS
                    if REDEDGE_KEYS[b[0]] not in spec.drop_bands.get(idx, [])
                ]
                tr = CaptureTruth(
                    idx,
                    cid,
                    t,
                    lat,
                    lon,
                    alt,
                    agl,
                    ln,
                    is_turn,
                    [REDEDGE_KEYS[b[0]] for b in bands],
                )
                set_no = flight_no
                sub = f"{(idx - index_offset) // 200:03d}"
                img_no = (idx - index_offset) % 200 + ((idx - index_offset) // 200) * 200
                for copy in range(n_copies):
                    for suffix, name, wl, rig in bands:
                        if copy == 0 and idx in spec.odd_name_captures:
                            fname = f"IM{rng.randint(0, 0xFFF):03X}{suffix}_X.TIF"
                        elif copy == 0:
                            fname = f"IMG_{img_no:04d}_{suffix}.tif"
                        else:
                            fname = f"IMG_{img_no:04d}_{suffix} - 복사본.tif"
                        p = root / f"{set_no:04d}SET" / sub / fname
                        sat = spec.saturated_fraction.get(idx, 0.0)
                        make_tiff(
                            p,
                            width=spec.image_size[0],
                            height=spec.image_size[1],
                            fill=20000 + rig * 1000,
                            saturated_fraction=sat,
                            seed=idx * 10 + rig,
                            panel_rect=on_ground and spec.panel_on_ground,
                        )
                        files.append(p)
                        tr.files.append(p)
                        sol_el, sol_az = _solar_rad(t, lat, lon)
                        sol_el += math.radians(spec.solar_elevation_offset_deg)
                        commands.append(
                            [
                                "-IFD0:Make=MicaSense",
                                "-IFD0:Model=RedEdge-M",
                                "-IFD0:Software=v7.3.2",
                                f"-ExifIFD:DateTimeOriginal={_fmt_dt(t)}",
                                f"-ExifIFD:SubSecTime={t.microsecond:06d}",
                                "-ExifIFD:ExposureTime=1/5556",
                                "-ExifIFD:ISOSpeed=100",
                                "-ExifIFD:FocalLength=5.5",
                                f"-GPS:GPSLatitude={abs(lat)}",
                                f"-GPS:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                                f"-GPS:GPSLongitude={abs(lon)}",
                                f"-GPS:GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
                                f"-GPS:GPSAltitude={alt:.3f}",
                                "-GPS:GPSAltitudeRef=0",
                                "-GPS:GPSDOP=0",
                                f"-XMP-Camera:BandName={name}",
                                f"-XMP-Camera:CentralWavelength={wl}",
                                f"-XMP-Camera:RigCameraIndex={rig}",
                                "-XMP-Camera:RigName=RedEdge-M",
                                "-XMP-Camera:PerspectiveFocalLength=5.4312474",
                                f"-XMP-Camera:Irradiance={110.0 * irr_scale:.4f}",
                                "-XMP-Camera:GPSXYAccuracy=1.06",
                                "-XMP-Camera:GPSZAccuracy=1.97",
                                f"-XMP-MicaSense:CaptureId={cid}",
                                f"-XMP-MicaSense:FlightId=synthFlight{flight_no}",
                                "-XMP-MicaSense:TriggerMethod=2",
                                f"-XMP-MicaSense:PressureAlt={alt - 76:.2f}",
                                f"-XMP-DLS:HorizontalIrradiance={90.0 * irr_scale:.4f}",
                                f"-XMP-DLS:SpectralIrradiance={110.0 * irr_scale:.4f}",
                                f"-XMP-DLS:SolarElevation={sol_el:.6f}",
                                f"-XMP-DLS:SolarAzimuth={sol_az:.6f}",
                                f"-XMP-DLS:Yaw={math.radians(heading):.6f}",
                                f"-XMP-DLS:Pitch={math.radians(tilt):.6f}",
                                "-XMP-DLS:Roll=0.0",
                                "-XMP-DLS:Serial=DA04-SYNTH",
                                str(p),
                            ]
                        )
                        # RedEdge 는 UTC 숫자를 FAT 로컬 시각에 그대로 쓴다 → mtime 은 UTC−offset
                        utime_plan.append((p, t.timestamp() - spec.utc_offset_min * 60))
                truth.append(tr)
            else:
                uuid = "".join(rng.choice("0123456789abcdef") for _ in range(32))
                keep = [b for b in M3M_BANDS if M3M_KEYS[b[0]] not in spec.drop_bands.get(idx, [])]
                has_rgb = "rgb" not in spec.drop_bands.get(idx, [])
                bands_keys = [M3M_KEYS[b[0]] for b in keep] + (["rgb"] if has_rgb else [])
                tr = CaptureTruth(idx, uuid, t, lat, lon, alt, agl, ln, is_turn, bands_keys)
                local = t + timedelta(minutes=spec.utc_offset_min)
                mission_ts = start + timedelta(minutes=spec.utc_offset_min)
                mission = root / f"DJI_{mission_ts:%Y%m%d%H%M}_{flight_no:03d}_합성-경로"
                base = f"DJI_{local:%Y%m%d%H%M%S}_{idx - index_offset + 1:04d}"
                flag = spec.rtk_flag.get(idx, 50)
                yaw_signed = heading - 360 if heading > 180 else heading
                common = [
                    "-IFD0:Make=DJI",
                    "-IFD0:Model=M3M",
                    "-IFD0:Software=11.09.10.58",
                    f"-ExifIFD:DateTimeOriginal={_fmt_dt(local)}",
                    f"-GPS:GPSLatitude={abs(lat)}",
                    f"-GPS:GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
                    f"-GPS:GPSLongitude={abs(lon)}",
                    f"-GPS:GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
                    f"-GPS:GPSAltitude={alt:.3f}",
                    "-GPS:GPSAltitudeRef=0",
                    f"-XMP-drone-dji:CaptureUUID={uuid}",
                    "-XMP-drone-dji:GpsStatus=RTK",
                    "-XMP-drone-dji:AltitudeType=RtkAlt",
                    f"-XMP-drone-dji:RtkFlag={flag}",
                    "-XMP-drone-dji:RtkStdLat=0.0104",
                    "-XMP-drone-dji:RtkStdLon=0.0093",
                    f"-XMP-drone-dji:RtkStdHgt={0.0214 if flag == 50 else 0.8:.4f}",
                    "-XMP-drone-dji:RtkDiffAge=1.6",
                    f"-XMP-drone-dji:UTCAtExposure={_fmt_dt(t)}.{t.microsecond:06d}",
                    f"-XMP-drone-dji:AbsoluteAltitude={alt:+.3f}",
                    f"-XMP-drone-dji:RelativeAltitude={agl:+.3f}",
                    f"-XMP-drone-dji:GimbalPitchDegree={-90.0 + tilt:+.1f}",
                    "-XMP-drone-dji:GimbalRollDegree=+0.00",
                    f"-XMP-drone-dji:GimbalYawDegree={yaw_signed:+.1f}",
                    f"-XMP-drone-dji:FlightYawDegree={yaw_signed:+.1f}",
                    "-XMP-drone-dji:FlightPitchDegree=-1.7",
                    "-XMP-drone-dji:FlightRollDegree=+1.8",
                    f"-XMP-drone-dji:FlightXSpeed={spec.speed_mps:.1f}",
                    "-XMP-drone-dji:FlightYSpeed=0.0",
                    "-XMP-drone-dji:SurveyingMode=1",
                    "-XMP-Camera:RigName=M3M",
                ]
                for copy in range(n_copies):
                    dup = "" if copy == 0 else " - 복사본"
                    for suffix, name, wl, rig, src in keep:
                        p = mission / f"{base}_MS_{suffix}{dup}.TIF"
                        make_tiff(
                            p,
                            width=spec.image_size[0],
                            height=spec.image_size[1],
                            fill=20000 + rig * 1000,
                            saturated_fraction=spec.saturated_fraction.get(idx, 0.0),
                            seed=idx * 10 + rig,
                            panel_rect=on_ground and spec.panel_on_ground,
                        )
                        files.append(p)
                        tr.files.append(p)
                        commands.append(
                            [
                                *common,
                                "-ExifIFD:ExposureTime=1/500",
                                "-ExifIFD:ISO=170",
                                f"-XMP-drone-dji:BandName={name}",
                                f"-XMP-drone-dji:ImageSource={src}",
                                f"-XMP-drone-dji:Irradiance={11486.0 * irr_scale:.3f}",
                                "-XMP-drone-dji:SensorGain=1.663",
                                "-XMP-drone-dji:BlackLevel=3200",
                                "-XMP-Camera:IrradianceExposureTime=0.182",
                                "-XMP-Camera:IrradianceGain=64.0",
                                f"-XMP-Camera:CentralWavelength={wl}",
                                f"-XMP-Camera:RigCameraIndex={rig}",
                                str(p),
                            ]
                        )
                        utime_plan.append((p, t.timestamp()))
                    if has_rgb:
                        p = mission / f"{base}_D{dup}.JPG"
                        make_jpeg(p, width=spec.image_size[0], height=spec.image_size[1])
                        files.append(p)
                        tr.files.append(p)
                        commands.append(
                            [
                                *common,
                                "-ExifIFD:ExposureTime=1/800",
                                "-ExifIFD:ISO=100",
                                "-XMP-Camera:RigCameraIndex=5",
                                str(p),
                            ]
                        )
                        utime_plan.append((p, t.timestamp()))
                truth.append(tr)
            idx += 1
        return idx

    n = one_flight(spec.start_utc, 0, 0)
    if spec.second_flight_gap_min is not None:
        n_pts = len(_positions(spec)) + spec.ground_captures + spec.ground_captures_post
        total_s = n_pts * spec.interval_s
        start2 = spec.start_utc + timedelta(seconds=total_s, minutes=spec.second_flight_gap_min)
        one_flight(start2, 1, n)

    _write_tags(exiftool, commands)
    for p, ts in utime_plan:
        import os

        os.utime(p, (ts, ts))
    # 손상·0 바이트 주입 (태그 쓴 뒤에)
    for i in spec.corrupt_files:
        p = truth[i].files[0]
        p.write_bytes(b"\x00" * 64)
    for i in spec.zero_byte_files:
        p = truth[i].files[-1]
        p.write_bytes(b"")
    return BuildResult(root=root, spec=spec, truth=truth, files=files)


def _write_tags(exiftool: Path, commands: list[list[str]]) -> None:
    if not commands:
        return
    lines: list[str] = []
    for i, cmd in enumerate(commands):
        if i:
            lines.append(
                "-execute"
            )  # 마지막 명령 뒤에는 붙이지 않는다 (빈 명령 → "No file specified")
        lines.extend(cmd)
    lines += ["-common_args", "-charset", "filename=utf8", "-overwrite_original", "-q"]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".args", delete=False) as fh:
        fh.write("\n".join(lines) + "\n")
        argfile = fh.name
    try:
        proc = subprocess.run(
            [str(exiftool), "-config", str(EXIFTOOL_CONFIG), "-@", argfile],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    finally:
        Path(argfile).unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"exiftool 태그 쓰기 실패 (rc={proc.returncode}): {proc.stderr[-2000:]}")
