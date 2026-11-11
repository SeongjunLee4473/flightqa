"""센서 플러그인 계약 (CLAUDE.md §3.2).

각 센서는 (1) 파일명으로 잠정 캡처 묶음을 만들고 (2) exiftool ``-G1 -n -struct`` 원본 태그로
캡처 키·밴드를 확정한 뒤 (3) 공통 스키마 ``CaptureMeta`` 로 정규화한다.
태그 이름은 반드시 ``docs/tags_<sensor>_numeric.json`` 에 있는 것만 쓴다.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flightqa.core.models import BandMeta, BandSpec, CameraModel, CaptureMeta, SensorKind

Raw = dict[str, Any]
"""exiftool -j -G1 -n -struct -a 한 파일의 출력."""

IMAGE_EXTENSIONS = {".tif", ".tiff", ".jpg", ".jpeg"}


# --------------------------------------------------------------------------- 값 변환 헬퍼


def as_float(v: Any) -> float | None:
    """exiftool 값(숫자·"+1.70"·"1/500"·리스트)을 float 로. 실패하면 None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v) if math.isfinite(float(v)) else None
    if isinstance(v, list):
        return as_float(v[0]) if v else None
    s = str(v).strip()
    if not s:
        return None
    if "/" in s:
        num, _, den = s.partition("/")
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    # "4800 4800 4800 4800" 처럼 공백 구분 다중값은 첫 값
    s = s.split()[0]
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def as_int(v: Any) -> int | None:
    f = as_float(v)
    return int(f) if f is not None else None


def as_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def rad_to_deg(v: Any) -> float | None:
    f = as_float(v)
    return math.degrees(f) if f is not None else None


_DT_RE = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_exif_datetime(
    value: Any,
    *,
    subsec: Any = None,
    assume_tz: timezone | None = None,
) -> datetime | None:
    """``YYYY:MM:DD HH:MM:SS[.fff][+HH:MM]`` 를 aware datetime 으로.

    오프셋이 문자열에 없으면 ``assume_tz`` 를 붙이고, 그것도 없으면 naive 로 반환한다.
    ``subsec`` 은 EXIF SubSecTime 문자열(자릿수 = 소수 자릿수).
    """
    s = as_str(value)
    if s is None:
        return None
    m = _DT_RE.match(s)
    if not m:
        return None
    y, mo, d, hh, mm, ss, frac, tz = m.groups()
    micro = 0
    if frac:
        micro = round(int(frac) / 10 ** len(frac) * 1_000_000)
    elif subsec is not None:
        sub = str(subsec).strip()
        if sub.isdigit():
            micro = round(int(sub) / 10 ** len(sub) * 1_000_000)
    micro = min(micro, 999_999)
    tzinfo: timezone | None = assume_tz
    if tz == "Z":
        tzinfo = UTC
    elif tz:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        tzinfo = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    try:
        return datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss), micro, tzinfo=tzinfo)
    except ValueError:
        return None


def is_image_file(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


# --------------------------------------------------------------------------- 플러그인 계약


class SensorPlugin(ABC):
    kind: SensorKind
    display_name: str
    bands: Sequence[BandSpec]
    camera: CameraModel
    filename_re: re.Pattern[str]

    @property
    def band_keys(self) -> list[str]:
        return [b.key for b in self.bands]

    @property
    def spectral_band_keys(self) -> list[str]:
        return [b.key for b in self.bands if not b.is_rgb]

    def band_by_name(self, name: str) -> BandSpec | None:
        n = name.strip().lower().replace(" ", "").replace("-", "")
        for b in self.bands:
            if b.name.lower().replace(" ", "").replace("-", "") == n:
                return b
        return None

    def band_by_rig_index(self, idx: int | None) -> BandSpec | None:
        if idx is None:
            return None
        for b in self.bands:
            if b.rig_index == idx:
                return b
        return None

    # -- 발견 단계 (파일명만) --------------------------------------------------

    @abstractmethod
    def provisional(self, path: Path, root: Path) -> tuple[str, str] | None:
        """파일명으로 (잠정 캡처 키, 밴드 키). 규칙에 안 맞으면 None."""

    def detect(self, files: Sequence[Path], root: Path) -> float:
        """이 폴더가 이 센서 데이터일 확신도 (0–1): 이미지 파일 중 파일명 규칙 일치 비율."""
        images = [f for f in files if is_image_file(f)]
        if not images:
            return 0.0
        hits = sum(1 for f in images if self.provisional(f, root) is not None)
        return hits / len(images)

    def group_of(self, path: Path, root: Path) -> str:
        """보고서용 그룹명 (SET 폴더 / 미션 폴더)."""
        try:
            rel = path.parent.relative_to(root)
        except ValueError:
            return path.parent.name
        return rel.as_posix() or "."

    # -- 추출 후 (태그) --------------------------------------------------------

    @abstractmethod
    def capture_key(self, raw: Raw, path: Path, root: Path) -> str | None:
        """태그 기준 캡처 키. 태그가 없으면 파일명 잠정 키로 폴백, 그것도 안 되면 None."""

    @abstractmethod
    def band_key(self, raw: Raw, path: Path) -> str | None:
        """태그 기준 밴드 키 (BandName 우선, RigCameraIndex, 파일명 순)."""

    @abstractmethod
    def parse(self, capture_key: str, group: str, records: list[tuple[Path, Raw]]) -> CaptureMeta:
        """밴드별 (경로, 원본 태그) 를 공통 스키마로 정규화한다."""

    # -- 공통 헬퍼 -------------------------------------------------------------

    def _band_meta_common(self, band: str, path: Path, raw: Raw) -> BandMeta:
        size = as_int(raw.get("System:FileSize"))
        mtime = parse_exif_datetime(raw.get("System:FileModifyDate"))
        return BandMeta(
            band=band,
            path=path,
            size_bytes=size,
            mtime_utc=mtime.astimezone(UTC) if mtime and mtime.tzinfo else mtime,
            width_px=as_int(raw.get("IFD0:ImageWidth") or raw.get("File:ImageWidth")),
            height_px=as_int(raw.get("IFD0:ImageHeight") or raw.get("File:ImageHeight")),
            bits=as_int(raw.get("IFD0:BitsPerSample") or raw.get("File:BitsPerSample")),
        )

    def finalize(self, meta: CaptureMeta) -> CaptureMeta:
        """공통 후처리: 누락 밴드 계산."""
        meta.missing_bands = [k for k in self.band_keys if k not in meta.bands]
        return meta
