"""공용 pytest 픽스처. 합성 데이터셋은 세션당 한 번만 만든다 (exiftool 쓰기 ~3 s)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from flightqa.core.exiftool import ExifToolNotFoundError, find_exiftool

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))  # `fixtures` 패키지 import 용

from fixtures.synth import BuildResult, FlightSpec, build_dataset, make_tiff  # noqa: E402

DOCS = TESTS.parent / "docs"


@pytest.fixture(scope="session")
def exiftool_exe() -> Path:
    """exiftool 이 없으면 해당 테스트를 건너뛴다(조용히 통과시키지 않는다)."""
    try:
        return find_exiftool()
    except ExifToolNotFoundError as e:
        pytest.skip(f"exiftool 없음: {e}")


@pytest.fixture
def synth_tiff(tmp_path: Path) -> Path:
    return make_tiff(tmp_path / "IMG_0000_1.tif", width=64, height=48)


@pytest.fixture(scope="session")
def docs_rededge() -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads(
        (DOCS / "tags_rededge_mx_numeric.json").read_text(encoding="utf-8")
    )
    return data


@pytest.fixture(scope="session")
def docs_m3m() -> list[dict[str, object]]:
    data: list[dict[str, object]] = json.loads(
        (DOCS / "tags_dji_m3m_numeric.json").read_text(encoding="utf-8")
    )
    return data


# --------------------------------------------------------------------------- 합성 데이터셋

RE_SPEC = FlightSpec(
    sensor="rededge_mx",
    n_lines=3,
    captures_per_line=8,
    ground_captures=2,
    drop_bands={5: ["nir"]},
    gap_after={12: 30.0},
    duplicate_captures=[3],
    corrupt_files=[7],
    agl_offset={20: -50.0},
    off_nadir_deg={9: 12.0},
    odd_name_captures=[15],
)
M3_SPEC = FlightSpec(
    sensor="dji_m3m",
    n_lines=2,
    captures_per_line=6,
    rtk_flag={4: 34, 5: 34},
    drop_bands={2: ["rgb"]},
    second_flight_gap_min=15.0,
)
CLEAN_SPEC = FlightSpec(
    sensor="rededge_mx",
    n_lines=2,
    captures_per_line=6,
    seed=7,
    ground_captures=1,
    ground_captures_post=1,
)
M2_SPEC = FlightSpec(
    sensor="rededge_mx",
    n_lines=3,
    captures_per_line=10,
    ground_captures=2,
    line_spacing_m=30.0,
    seed=11,
    start_utc=datetime(2026, 6, 1, 3, 0, tzinfo=UTC),  # 12:00 KST, 태양고도 ≈ 74°
    saturated_fraction={5: 0.02, 6: 0.001},
    irradiance_scale={14: 0.5, 15: 0.5, 16: 0.6},
    solar_elevation_offset_deg=0.0,
)


@pytest.fixture(scope="session")
def synth_re(tmp_path_factory: pytest.TempPathFactory, exiftool_exe: Path) -> BuildResult:
    return build_dataset(tmp_path_factory.mktemp("synth_re"), RE_SPEC, exiftool_exe)


@pytest.fixture(scope="session")
def synth_m3(tmp_path_factory: pytest.TempPathFactory, exiftool_exe: Path) -> BuildResult:
    return build_dataset(tmp_path_factory.mktemp("synth_m3"), M3_SPEC, exiftool_exe)


@pytest.fixture(scope="session")
def synth_clean(tmp_path_factory: pytest.TempPathFactory, exiftool_exe: Path) -> BuildResult:
    return build_dataset(tmp_path_factory.mktemp("synth_clean"), CLEAN_SPEC, exiftool_exe)


@pytest.fixture(scope="session")
def synth_m2(tmp_path_factory: pytest.TempPathFactory, exiftool_exe: Path) -> BuildResult:
    return build_dataset(tmp_path_factory.mktemp("synth_m2"), M2_SPEC, exiftool_exe)
