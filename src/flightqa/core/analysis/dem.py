"""DEM/DSM GeoTIFF 표본 추출 (CLAUDE.md §4.3 "DEM 경로를 주면 실제 AGL").

- 의존성은 tifffile + pyproj 만 쓴다 (GDAL/rasterio 없이, 오프라인 exe 배포 부담 없음).
- 지원: 단일 밴드, ModelPixelScale+ModelTiepoint 또는 ModelTransformation, 투영/지리 좌표계
  (GeoKey 3072 ProjectedCSType / 2048 GeographicType), GDAL_NODATA. 타일·스트립 모두 ``asarray``
  로 한 번에 읽는다 (조사지 DSM 수천×수천 픽셀 = 수십 MB).
- 표본은 쌍선형 보간. 격자 밖·nodata 는 NaN.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from pyproj import CRS, Transformer

NAN = float("nan")


class DemError(ValueError):
    """DEM 파일을 읽을 수 없거나 지리참조 정보가 없다."""


class DemSampler:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise DemError(f"DEM 파일이 없음: {self.path}")
        try:
            with tifffile.TiffFile(self.path) as tf:
                page = tf.pages[0]
                if not isinstance(page, tifffile.TiffPage) or not page.is_geotiff:
                    raise DemError(f"GeoTIFF 가 아님 (지리참조 태그 없음): {self.path}")
                geo: dict[str, Any] = page.geotiff_tags or {}
                arr = np.asarray(page.asarray(), dtype=np.float64)
                nodata_tag = page.tags.get("GDAL_NODATA")
        except (OSError, ValueError, tifffile.TiffFileError) as e:
            raise DemError(f"DEM 읽기 실패 {self.path}: {e}") from e
        if arr.ndim == 3:
            arr = arr[..., 0] if arr.shape[-1] <= 4 else arr[0]
        if arr.ndim != 2:
            raise DemError(f"DEM 은 2차원 단일 밴드여야 함: shape={arr.shape}")
        if nodata_tag is not None:
            try:
                nd = float(str(nodata_tag.value).strip())
                arr[arr == nd] = np.nan
            except ValueError:
                pass
        self.arr = arr
        self.rows, self.cols = arr.shape
        # 픽셀 (col,row) → 지도 좌표. 좌상단 모서리 기준 (raster-is-area 관례) 로 통일한다.
        tr = geo.get("ModelTransformation")
        if tr is not None:
            m = np.asarray(tr, dtype=float).reshape(4, 4)
            self.a, self.b, self.c = float(m[0, 0]), float(m[0, 1]), float(m[0, 3])
            self.d, self.e, self.f = float(m[1, 0]), float(m[1, 1]), float(m[1, 3])
        else:
            scale = geo.get("ModelPixelScale")
            tie = geo.get("ModelTiepoint")
            if scale is None or tie is None:
                raise DemError("ModelTransformation 또는 ModelPixelScale+ModelTiepoint 가 필요함")
            sx, sy = float(scale[0]), float(scale[1])
            i, j, _k, x, y = (float(v) for v in tie[:5])
            self.a, self.b, self.c = sx, 0.0, x - i * sx
            self.d, self.e, self.f = 0.0, -sy, y + j * sy
        if (
            geo.get("GTRasterTypeGeoKey", 1) == 2
        ):  # RasterPixelIsPoint → 좌상단 모서리로 반 픽셀 이동
            self.c -= 0.5 * self.a + 0.5 * self.b
            self.f -= 0.5 * self.d + 0.5 * self.e
        model = int(geo.get("GTModelTypeGeoKey", 1))
        epsg = geo.get("ProjectedCSTypeGeoKey") if model == 1 else geo.get("GeographicTypeGeoKey")
        if epsg is None or int(epsg) in (0, 32767):
            raise DemError("DEM 의 EPSG 코드를 알 수 없음 (GeoKey 3072/2048)")
        self.epsg = int(epsg)
        self.crs = CRS.from_epsg(self.epsg)
        self._tr = Transformer.from_crs(4326, self.crs, always_xy=True)
        det = self.a * self.e - self.b * self.d
        if det == 0:
            raise DemError("DEM 지리변환 행렬이 특이함")
        self._inv = (self.e / det, -self.b / det, -self.d / det, self.a / det)

    def describe(self) -> str:
        return f"{self.path.name} (EPSG:{self.epsg}, {self.cols}×{self.rows} px)"

    def sample(self, lons: Sequence[float | None], lats: Sequence[float | None]) -> list[float]:
        """위경도 목록 → 지표고도 (DEM 단위, 보통 m). 격자 밖·결측은 NaN."""
        out = [NAN] * len(lons)
        idx = [
            i
            for i, (lo, la) in enumerate(zip(lons, lats, strict=True))
            if lo is not None and la is not None
        ]
        if not idx:
            return out
        xs, ys = self._tr.transform([lons[i] for i in idx], [lats[i] for i in idx])
        ia, ib, ic, id_ = self._inv
        for k, i in enumerate(idx):
            u, v = float(xs[k]) - self.c, float(ys[k]) - self.f
            col = ia * u + ib * v - 0.5  # 픽셀 중심 기준으로 보간
            row = ic * u + id_ * v - 0.5
            out[i] = self._bilinear(col, row)
        return out

    def _bilinear(self, col: float, row: float) -> float:
        if not (0.0 <= col <= self.cols - 1 and 0.0 <= row <= self.rows - 1):
            # 반 픽셀 테두리는 가장자리 값으로
            if -0.5 <= col <= self.cols - 0.5 and -0.5 <= row <= self.rows - 0.5:
                col = min(max(col, 0.0), self.cols - 1.0)
                row = min(max(row, 0.0), self.rows - 1.0)
            else:
                return NAN
        c0, r0 = math.floor(col), math.floor(row)
        c1, r1 = min(c0 + 1, self.cols - 1), min(r0 + 1, self.rows - 1)
        fc, fr = col - c0, row - r0
        z00, z01 = self.arr[r0, c0], self.arr[r0, c1]
        z10, z11 = self.arr[r1, c0], self.arr[r1, c1]
        z = (1 - fr) * ((1 - fc) * z00 + fc * z01) + fr * ((1 - fc) * z10 + fc * z11)
        return float(z) if np.isfinite(z) else NAN
