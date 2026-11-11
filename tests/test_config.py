from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from flightqa.config.schema import default_config_dict, load_config
from flightqa.core.models import SensorKind


def test_defaults_load() -> None:
    cfg = load_config()
    assert cfg.flight.target_agl_m == "auto" and cfg.flight.target_speed_mps == "auto"
    assert cfg.discovery.flight_gap_min == 10.0
    assert cfg.rtk.fixed_flags == [50]
    assert cfg.flight.takeoff_alt_msl_m == "auto"
    cam = cfg.sensor(SensorKind.REDEDGE_MX).camera()
    assert cam.saturation_dn == 65520
    assert cam.gsd_m(120.0) == pytest.approx(0.0833, abs=0.001)
    w, h = cam.footprint_m(120.0)
    assert w == pytest.approx(106.7, abs=0.1) and h == pytest.approx(80.0, abs=0.1)
    m3 = cfg.sensor(SensorKind.DJI_M3M).camera()
    assert m3.gsd_m(120.0) == pytest.approx(0.0553, abs=0.001)


def test_user_override_deep_merge(tmp_path: Path) -> None:
    user = tmp_path / "u.toml"
    user.write_text(
        "[flight]\ntarget_agl_m = 80\ntakeoff_alt_msl_m = 55.5\n[rtk]\nfixed_flags = [50, 34]\n"
    )
    cfg = load_config(user)
    assert cfg.flight.target_agl_m == 80.0
    assert cfg.flight.takeoff_alt_msl_m == 55.5
    assert cfg.flight.agl_tolerance_warn_pct == 20.0  # 나머지는 기본값 유지
    assert cfg.rtk.fixed_flags == [50, 34]


def test_unknown_key_rejected(tmp_path: Path) -> None:
    user = tmp_path / "u.toml"
    user.write_text("[flight]\ntarget_agl = 80\n")  # 오타
    with pytest.raises(ValidationError):
        load_config(user)


def test_default_dict_has_all_sections() -> None:
    d = default_config_dict()
    assert {
        "discovery",
        "integrity",
        "flight",
        "rtk",
        "judgement",
        "cache",
        "extract",
        "sensors",
    } <= set(d)


def test_workers_and_cache_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load_config()
    assert cfg.extract.resolve_workers() >= 1
    cfg.extract.workers = 3
    assert cfg.extract.resolve_workers() == 3
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert cfg.cache.resolve_path() == tmp_path / "flightqa" / "cache.sqlite"
    cfg.cache.path = str(tmp_path / "x.sqlite")
    assert cfg.cache.resolve_path() == tmp_path / "x.sqlite"
