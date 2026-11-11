from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from fixtures.synth import SATURATION_DN, make_tiff


def test_saturation_dn_is_12bit_shifted() -> None:
    assert SATURATION_DN == 65520
    assert SATURATION_DN & 0xF == 0


def test_make_tiff_shape_and_saturation(tmp_path: Path) -> None:
    p = make_tiff(tmp_path / "a.tif", width=100, height=10, saturated_fraction=0.25)
    img = tifffile.imread(p)
    assert img.dtype == np.uint16
    assert img.shape == (10, 100)
    assert (img == SATURATION_DN).sum() == 250
