"""MicaSense RedEdge-MX (RedEdge-M 동일 태그 구조) 플러그인.

근거: docs/tags_rededge_mx_numeric.json (샘플 RedEdge-M v7.3.2, docs/ASSUMPTIONS.md).
- 파일: ``IMG_XXXX_B.tif`` (B = 1..5), 폴더 ``NNNNSET/NNN/``. 8.3 이름(``IM1ABD~1.TIF``)도 있음.
- 밴드는 파일 번호가 아니라 ``XMP-Camera:BandName`` 으로 정한다 (_4 = NIR, _5 = Red edge).
- 시각: ``ExifIFD:DateTimeOriginal`` 은 UTC, ``ExifIFD:SubSecTime`` 은 소수부 자릿수 문자열.
- DLS ``Yaw/Pitch/Roll``, ``SolarElevation/Azimuth`` 는 라디안.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from flightqa.core.models import (
    BandSpec,
    CameraModel,
    CaptureMeta,
    ExposureInfo,
    SensorKind,
)
from flightqa.core.sensors.base import (
    Raw,
    SensorPlugin,
    as_float,
    as_int,
    as_str,
    is_image_file,
    parse_exif_datetime,
    rad_to_deg,
)

_FILENAME = re.compile(r"^IMG_(\d{4})_([1-5])\.tiff?$", re.IGNORECASE)
_SUFFIX_TO_KEY = {"1": "blue", "2": "green", "3": "red", "4": "nir", "5": "rededge"}


class RedEdgeMX(SensorPlugin):
    kind = SensorKind.REDEDGE_MX
    display_name = "MicaSense RedEdge-MX"
    filename_re = _FILENAME
    bands = (
        BandSpec(key="blue", name="Blue", center_nm=475, fwhm_nm=32, rig_index=0),
        BandSpec(key="green", name="Green", center_nm=560, fwhm_nm=27, rig_index=1),
        BandSpec(key="red", name="Red", center_nm=668, fwhm_nm=14, rig_index=2),
        BandSpec(key="nir", name="NIR", center_nm=842, fwhm_nm=57, rig_index=3),
        BandSpec(key="rededge", name="Red edge", center_nm=717, fwhm_nm=12, rig_index=4),
    )
    camera = CameraModel(
        sensor_width_mm=4.8,
        sensor_height_mm=3.6,
        focal_length_mm=5.4,
        image_width_px=1280,
        image_height_px=960,
    )

    def __init__(self, camera: CameraModel | None = None) -> None:
        if camera is not None:
            self.camera = camera

    # -- 발견 ------------------------------------------------------------------

    def provisional(self, path: Path, root: Path) -> tuple[str, str] | None:
        m = self.filename_re.match(path.name)
        if not m:
            return None
        return f"{self.group_of(path, root)}/IMG_{m.group(1)}", _SUFFIX_TO_KEY[m.group(2)]

    def detect(self, files: Sequence[Path], root: Path) -> float:
        score = super().detect(files, root)
        # SET 폴더 구조가 보이면 가산
        if score > 0 and any(re.search(r"\d{4}SET", str(f)) for f in files[:200]):
            score = min(1.0, score + 0.05)
        return score

    # -- 태그 ------------------------------------------------------------------

    def capture_key(self, raw: Raw, path: Path, root: Path) -> str | None:
        cid = as_str(raw.get("XMP-MicaSense:CaptureId"))
        if cid:
            return cid
        prov = self.provisional(path, root)
        return prov[0] if prov else None

    def band_key(self, raw: Raw, path: Path) -> str | None:
        name = as_str(raw.get("XMP-Camera:BandName"))
        if name:
            spec = self.band_by_name(name)
            if spec:
                return spec.key
        spec = self.band_by_rig_index(as_int(raw.get("XMP-Camera:RigCameraIndex")))
        if spec:
            return spec.key
        m = self.filename_re.match(path.name)
        return _SUFFIX_TO_KEY[m.group(2)] if m else None

    def parse(self, capture_key: str, group: str, records: list[tuple[Path, Raw]]) -> CaptureMeta:
        meta = CaptureMeta(capture_id=capture_key, sensor=self.kind, group=group)
        first: Raw | None = None
        for path, raw in records:
            band = self.band_key(raw, path)
            if band is None:
                meta.warnings.append(f"밴드를 식별할 수 없어 제외: {path.name}")
                continue
            if band in meta.bands:
                meta.warnings.append(f"밴드 {band} 중복 파일: {path.name} (먼저 온 파일 사용)")
                continue
            bm = self._band_meta_common(band, path, raw)
            bm.time_utc = parse_exif_datetime(
                raw.get("ExifIFD:DateTimeOriginal"),
                subsec=raw.get("ExifIFD:SubSecTime"),
                assume_tz=UTC,
            )
            bm.exposure = ExposureInfo(
                exposure_s=as_float(raw.get("ExifIFD:ExposureTime")),
                iso=as_float(raw.get("ExifIFD:ISOSpeed") or raw.get("ExifIFD:ISO")),
                gain=None,
            )
            bm.irradiance = as_float(raw.get("XMP-DLS:HorizontalIrradiance"))
            bm.irradiance_raw = as_float(
                raw.get("XMP-DLS:SpectralIrradiance") or raw.get("XMP-Camera:Irradiance")
            )
            bm.black_level = as_float(raw.get("IFD0:BlackLevel"))
            meta.bands[band] = bm
            if first is None:
                first = raw

        if first is None:
            return self.finalize(meta)

        # 캡처 공통 값은 첫 밴드 파일에서 (5 개 파일이 같은 값을 가진다)
        times = [b.time_utc for b in meta.bands.values() if b.time_utc]
        meta.time_utc = min(times) if times else None
        meta.utc_offset_min = None  # 태그에 로컬 시각 정보 없음 → 파이프라인이 채움
        meta.lat = as_float(first.get("Composite:GPSLatitude"))
        meta.lon = as_float(first.get("Composite:GPSLongitude"))
        meta.alt_msl_m = as_float(first.get("Composite:GPSAltitude"))
        if meta.alt_msl_m is None:
            meta.alt_msl_m = as_float(first.get("GPS:GPSAltitude"))
        meta.pressure_alt_m = as_float(first.get("XMP-MicaSense:PressureAlt"))
        meta.yaw_deg = rad_to_deg(first.get("XMP-DLS:Yaw"))
        meta.pitch_deg = rad_to_deg(first.get("XMP-DLS:Pitch"))
        meta.roll_deg = rad_to_deg(first.get("XMP-DLS:Roll"))
        meta.solar_elevation_deg = rad_to_deg(first.get("XMP-DLS:SolarElevation"))
        meta.solar_azimuth_deg = rad_to_deg(first.get("XMP-DLS:SolarAzimuth"))
        trig = as_int(first.get("XMP-MicaSense:TriggerMethod"))
        meta.trigger = None if trig is None else str(trig)
        meta.flight_tag = as_str(first.get("XMP-MicaSense:FlightId"))
        meta.model = as_str(first.get("IFD0:Model"))
        meta.firmware = as_str(first.get("IFD0:Software"))
        meta.gps_xy_accuracy_m = as_float(first.get("XMP-Camera:GPSXYAccuracy"))
        meta.gps_z_accuracy_m = as_float(first.get("XMP-Camera:GPSZAccuracy"))
        meta.rtk = None  # Quick Mount: 기체 RTK 미주입 (CLAUDE.md §2.1)
        meta.extras = {
            "dls_serial": as_str(first.get("XMP-DLS:Serial")),
            "dls_sw_version": as_str(first.get("XMP-DLS:SwVersion")),
            "irradiance_yaw_deg": as_float(first.get("XMP-Camera:IrradianceYaw")),
            "irradiance_pitch_deg": as_float(first.get("XMP-Camera:IrradiancePitch")),
            "irradiance_roll_deg": as_float(first.get("XMP-Camera:IrradianceRoll")),
            "direct_irradiance": as_float(first.get("XMP-DLS:DirectIrradiance")),
            "scattered_irradiance": as_float(first.get("XMP-DLS:ScatteredIrradiance")),
            "gps_dop": as_float(first.get("GPS:GPSDOP")),
            "boot_timestamp": as_int(first.get("XMP-MicaSense:BootTimestamp")),
        }
        return self.finalize(meta)


def looks_like_rededge_file(path: Path) -> bool:
    """8.3 이름 등 규칙 불일치 TIFF 를 후보로 볼지."""
    return is_image_file(path) and path.suffix.lower() in {".tif", ".tiff"}
