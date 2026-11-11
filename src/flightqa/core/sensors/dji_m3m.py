"""DJI Mavic 3 Multispectral (M3M) 플러그인.

근거: docs/tags_dji_m3m_numeric.json (펌웨어 11.09.10.58, docs/ASSUMPTIONS.md).
- 파일: ``DJI_YYYYMMDDHHMMSS_NNNN_D.JPG`` (RGB) + ``_MS_{G,R,RE,NIR}.TIF``. 미션 폴더명에 한글 가능.
- 캡처 키: ``XMP-drone-dji:CaptureUUID`` (JPG 와 4 TIF 동일). 없으면 파일명 기반.
- 시각: ``XMP-drone-dji:UTCAtExposure`` (µs, UTC). ``ExifIFD:DateTimeOriginal`` 은 로컬.
- RTK: ``GpsStatus``, ``RtkFlag`` (50 = Fix 로 가정), ``RtkStdLat/Lon/Hgt`` (m), ``RtkDiffAge`` (s).
- 조도: ``XMP-drone-dji:Irradiance`` 카운트 + ``XMP-Camera:IrradianceExposureTime/Gain``.
"""

from __future__ import annotations

import math
import re
from datetime import UTC
from pathlib import Path

from flightqa.core.models import (
    BandSpec,
    CameraModel,
    CaptureMeta,
    ExposureInfo,
    RtkInfo,
    SensorKind,
)
from flightqa.core.sensors.base import (
    Raw,
    SensorPlugin,
    as_float,
    as_int,
    as_str,
    parse_exif_datetime,
)

_FILENAME = re.compile(
    r"^DJI_(\d{14})_(\d{4})_(?:(D)\.jpe?g|MS_(G|R|RE|NIR)\.tiff?)$", re.IGNORECASE
)
_SUFFIX_TO_KEY = {"G": "green", "R": "red", "RE": "rededge", "NIR": "nir", "D": "rgb"}
RTK_FIXED_FLAGS = frozenset({50})
"""DJI RtkFlag 50 = Fix (가정, docs/ASSUMPTIONS.md). 규칙 엔진은 config.rtk.fixed_flags 를 쓴다."""


class DjiM3M(SensorPlugin):
    kind = SensorKind.DJI_M3M
    display_name = "DJI Mavic 3 Multispectral"
    filename_re = _FILENAME
    bands = (
        BandSpec(key="green", name="Green", center_nm=560, fwhm_nm=16, rig_index=1),
        BandSpec(key="red", name="Red", center_nm=650, fwhm_nm=16, rig_index=2),
        BandSpec(key="rededge", name="RedEdge", center_nm=730, fwhm_nm=16, rig_index=3),
        BandSpec(key="nir", name="NIR", center_nm=860, fwhm_nm=26, rig_index=4),
        BandSpec(key="rgb", name="RGB", center_nm=0, rig_index=5, is_rgb=True),
    )
    camera = CameraModel(
        sensor_width_mm=5.184,
        sensor_height_mm=3.888,
        focal_length_mm=4.34,
        image_width_px=2592,
        image_height_px=1944,
    )

    def __init__(self, camera: CameraModel | None = None) -> None:
        if camera is not None:
            self.camera = camera

    # -- 발견 ------------------------------------------------------------------

    def provisional(self, path: Path, root: Path) -> tuple[str, str] | None:
        m = self.filename_re.match(path.name)
        if not m:
            return None
        suffix = m.group(3) or m.group(4)
        key = f"{self.group_of(path, root)}/DJI_{m.group(1)}_{m.group(2)}"
        return key, _SUFFIX_TO_KEY[suffix.upper()]

    # -- 태그 ------------------------------------------------------------------

    def capture_key(self, raw: Raw, path: Path, root: Path) -> str | None:
        uuid = as_str(raw.get("XMP-drone-dji:CaptureUUID"))
        if uuid:
            return uuid
        prov = self.provisional(path, root)
        return prov[0] if prov else None

    def band_key(self, raw: Raw, path: Path) -> str | None:
        ftype = as_str(raw.get("File:FileType"))
        if ftype and ftype.upper() == "JPEG":
            return "rgb"
        name = as_str(raw.get("XMP-drone-dji:BandName"))
        if name:
            spec = self.band_by_name(name)
            if spec:
                return spec.key
        src = as_str(raw.get("XMP-drone-dji:ImageSource"))  # 예: MS_G_CAMERA
        if src:
            m = re.match(r"MS_(G|R|RE|NIR)_CAMERA", src.upper())
            if m:
                return _SUFFIX_TO_KEY[m.group(1)]
        spec = self.band_by_rig_index(as_int(raw.get("XMP-Camera:RigCameraIndex")))
        if spec:
            return spec.key
        m2 = self.filename_re.match(path.name)
        if m2:
            return _SUFFIX_TO_KEY[(m2.group(3) or m2.group(4)).upper()]
        return None

    def parse(self, capture_key: str, group: str, records: list[tuple[Path, Raw]]) -> CaptureMeta:
        meta = CaptureMeta(capture_id=capture_key, sensor=self.kind, group=group)
        ref: Raw | None = None
        for path, raw in records:
            band = self.band_key(raw, path)
            if band is None:
                meta.warnings.append(f"밴드를 식별할 수 없어 제외: {path.name}")
                continue
            if band in meta.bands:
                meta.warnings.append(f"밴드 {band} 중복 파일: {path.name} (먼저 온 파일 사용)")
                continue
            bm = self._band_meta_common(band, path, raw)
            bm.time_utc = parse_exif_datetime(raw.get("XMP-drone-dji:UTCAtExposure"), assume_tz=UTC)
            bm.exposure = ExposureInfo(
                exposure_s=as_float(raw.get("ExifIFD:ExposureTime")),
                iso=as_float(raw.get("ExifIFD:ISO")),
                gain=as_float(raw.get("XMP-drone-dji:SensorGain")),
            )
            irr_raw = as_float(raw.get("XMP-drone-dji:Irradiance"))
            irr_exp = as_float(raw.get("XMP-Camera:IrradianceExposureTime"))
            irr_gain = as_float(raw.get("XMP-Camera:IrradianceGain"))
            bm.irradiance_raw = irr_raw
            if irr_raw is not None and irr_exp and irr_gain:
                bm.irradiance = irr_raw / (irr_exp * irr_gain)
            else:
                bm.irradiance = irr_raw
            bm.black_level = as_float(raw.get("XMP-drone-dji:BlackLevel"))
            meta.bands[band] = bm
            # 캡처 공통값은 다중분광 TIF 를 우선 참조 (RGB 는 렌즈·시각이 약간 다름)
            if ref is None or (band != "rgb" and as_str(ref.get("File:FileType")) == "JPEG"):
                ref = raw

        if ref is None:
            return self.finalize(meta)

        times = [b.time_utc for b in meta.bands.values() if b.time_utc]
        meta.time_utc = min(times) if times else None
        local = parse_exif_datetime(ref.get("ExifIFD:DateTimeOriginal"))
        if meta.time_utc and local:
            delta = local.replace(tzinfo=None) - meta.time_utc.replace(tzinfo=None)
            meta.utc_offset_min = int(round(delta.total_seconds() / 60 / 15) * 15)
        meta.lat = as_float(ref.get("Composite:GPSLatitude"))
        meta.lon = as_float(ref.get("Composite:GPSLongitude"))
        meta.alt_msl_m = as_float(ref.get("XMP-drone-dji:AbsoluteAltitude"))
        if meta.alt_msl_m is None:
            meta.alt_msl_m = as_float(ref.get("Composite:GPSAltitude"))
        meta.alt_rel_m = as_float(ref.get("XMP-drone-dji:RelativeAltitude"))
        meta.yaw_deg = as_float(ref.get("XMP-drone-dji:FlightYawDegree"))
        meta.pitch_deg = as_float(ref.get("XMP-drone-dji:FlightPitchDegree"))
        meta.roll_deg = as_float(ref.get("XMP-drone-dji:FlightRollDegree"))
        meta.gimbal_pitch_deg = as_float(ref.get("XMP-drone-dji:GimbalPitchDegree"))
        meta.gimbal_roll_deg = as_float(ref.get("XMP-drone-dji:GimbalRollDegree"))
        meta.gimbal_yaw_deg = as_float(ref.get("XMP-drone-dji:GimbalYawDegree"))
        meta.model = as_str(ref.get("IFD0:Model"))
        meta.firmware = as_str(ref.get("IFD0:Software"))
        flag = as_int(ref.get("XMP-drone-dji:RtkFlag"))
        meta.rtk = RtkInfo(
            status=as_str(ref.get("XMP-drone-dji:GpsStatus")),
            flag=flag,
            fixed=None if flag is None else flag in RTK_FIXED_FLAGS,
            std_lat_m=as_float(ref.get("XMP-drone-dji:RtkStdLat")),
            std_lon_m=as_float(ref.get("XMP-drone-dji:RtkStdLon")),
            std_hgt_m=as_float(ref.get("XMP-drone-dji:RtkStdHgt")),
            diff_age_s=as_float(ref.get("XMP-drone-dji:RtkDiffAge")),
        )
        meta.extras = {
            "altitude_type": as_str(ref.get("XMP-drone-dji:AltitudeType")),
            "surveying_mode": as_int(ref.get("XMP-drone-dji:SurveyingMode")),
            "shutter_type": as_str(ref.get("XMP-drone-dji:ShutterType")),
            "sun_sensor": as_float(ref.get("XMP-Camera:SunSensor")),
            "sun_sensor_pitch_deg": as_float(ref.get("XMP-Camera:SunSensorPitch")),
            "sun_sensor_roll_deg": as_float(ref.get("XMP-Camera:SunSensorRoll")),
            "ls_status": as_int(ref.get("XMP-drone-dji:LS_status")),
            "flight_speed_mps": _speed(ref),
        }
        return self.finalize(meta)


def _speed(raw: Raw) -> float | None:
    vx = as_float(raw.get("XMP-drone-dji:FlightXSpeed"))
    vy = as_float(raw.get("XMP-drone-dji:FlightYSpeed"))
    if vx is None or vy is None:
        return None
    return math.hypot(vx, vy)
