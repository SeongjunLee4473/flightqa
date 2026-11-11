"""보고서 썸네일: 16-bit 밴드 TIFF → 8-bit 그레이스케일 PNG data URL. 의존성 없이 zlib 만 쓴다.

썸네일은 대표 캡처 몇 장(패널 후보·최대 포화·첫 조사 캡처)에만 쓰므로 속도보다 단순함을 택했다.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def encode_png_gray8(img: np.ndarray[Any, Any]) -> bytes:
    """2-D uint8 배열을 PNG 바이트로."""
    if img.ndim != 2 or img.dtype != np.uint8:
        raise ValueError("uint8 2-D 배열이 필요")
    h, w = img.shape
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(h))  # 필터 0
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)
    return (
        _SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")
    )


def thumbnail_data_url(path: Path, max_width: int = 240, sat_dn: int | None = None) -> str | None:
    """밴드 TIFF 를 p1–p99 스트레치한 그레이 PNG data URL 로.

    실패하면 None (보고서는 계속 만든다).
    """
    try:
        img = tifffile.imread(path)
    except Exception:  # noqa: BLE001 - 썸네일 실패는 보고서를 막지 않는다
        return None
    if img.ndim > 2:
        img = img.reshape(img.shape[0], img.shape[1], -1)[:, :, 0]
    step = max(1, int(np.ceil(img.shape[1] / max_width)))
    small = img[::step, ::step].astype(np.float64)
    lo, hi = np.percentile(small, [1, 99])
    if hi <= lo:
        hi = lo + 1.0
    stretched = np.clip((small - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    if sat_dn is not None:
        stretched[small >= sat_dn] = 255
    return "data:image/png;base64," + base64.b64encode(encode_png_gray8(stretched)).decode("ascii")
