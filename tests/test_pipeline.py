"""파이프라인 end-to-end: 합성 데이터에 주입한 이상이 의도한 판정으로 나오는지."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from fixtures.synth import BuildResult
from flightqa.config.schema import load_config
from flightqa.core import pipeline
from flightqa.core.metadata import CancelledError, CancelToken
from flightqa.core.models import Metric, RunReport, SensorKind, Status
from flightqa.report import export


def _metric(report: RunReport, flight: int, key: str) -> Metric:
    for g in report.flights[flight].groups:
        for m in g.metrics:
            if m.key == key:
                return m
    raise KeyError(key)


def _run_metric(report: RunReport, key: str) -> Metric:
    for g in report.run_groups:
        for m in g.metrics:
            if m.key == key:
                return m
    raise KeyError(key)


@pytest.fixture(scope="module")
def report_re(synth_re: BuildResult) -> RunReport:
    return pipeline.run(synth_re.root, load_config(), use_cache=False)


@pytest.fixture(scope="module")
def report_m3(synth_m3: BuildResult) -> RunReport:
    return pipeline.run(synth_m3.root, load_config(), use_cache=False)


@pytest.mark.exiftool
def test_rededge_overall(report_re: RunReport, synth_re: BuildResult) -> None:
    r = report_re
    assert r.sensor is SensorKind.REDEDGE_MX
    assert r.n_captures == 30
    assert len(r.flights) == 1
    assert r.status is Status.FAIL
    assert "게이트" in r.headline_detail and r.action_items
    assert r.exiftool_version is not None
    assert r.sample_tags is not None and "XMP-Camera:BandName" in r.sample_tags
    assert r.timings_s["total"] > 0


@pytest.mark.exiftool
def test_rededge_integrity_metrics(report_re: RunReport) -> None:
    m = _metric(report_re, 0, "integrity.missing_bands")
    assert m.status is Status.FAIL and m.gate
    assert m.details["n_incomplete"] == 2
    assert m.details["by_band"] == {"nir": 1, "blue": 1}
    gaps = _metric(report_re, 0, "integrity.gaps")
    assert gaps.value == 1 and gaps.status is Status.WARN
    assert gaps.details["gaps"][0]["index"] == 13
    assert gaps.details["gaps"][0]["gap_s"] == pytest.approx(33.8, abs=0.1)
    assert _metric(report_re, 0, "integrity.clock_reversal").value == 0
    tm = _metric(report_re, 0, "integrity.time_mismatch")
    assert tm.value == 0.0  # RedEdge UTC-in-FAT 관례를 보정하므로 불일치 없음
    assert _metric(report_re, 0, "integrity.interval_median").value == pytest.approx(3.8, abs=0.01)


@pytest.mark.exiftool
def test_rededge_files_metrics(report_re: RunReport) -> None:
    assert _run_metric(report_re, "files.corrupt").value == 1
    assert _run_metric(report_re, "files.corrupt").status is Status.WARN
    assert _run_metric(report_re, "files.duplicates").value == 5
    unresolved = _run_metric(report_re, "files.unresolved")
    assert unresolved.value == 10 and "편입 10개" in unresolved.reason


@pytest.mark.exiftool
def test_rededge_flight_metrics(report_re: RunReport, synth_re: BuildResult) -> None:
    fl = report_re.flights[0]
    s = fl.summary
    assert s.n_lines == 3
    assert s.mean_agl_m == pytest.approx(120.0, abs=0.5)
    assert s.median_speed_mps == pytest.approx(5.0, abs=0.05)
    assert s.median_interval_s == pytest.approx(3.8, abs=0.01)
    assert fl.takeoff_alt_msl_m == pytest.approx(100.0)  # 첫 캡처(지상) 고도
    assert "첫 캡처" in fl.agl_source
    assert _metric(report_re, 0, "flight.agl_mean").status is Status.PASS
    assert _metric(report_re, 0, "flight.agl_within_tolerance").status is Status.PASS
    assert _metric(report_re, 0, "flight.speed").status is Status.PASS
    lines = _metric(report_re, 0, "flight.lines")
    assert (lines.value == 3 and "29." in lines.reason) or "30." in lines.reason
    rtk = _metric(report_re, 0, "rtk.fixed_pct")
    assert rtk.status is Status.NA and "Quick Mount" in rtk.reason
    # 캡처 행: 연직 이탈 12° 는 idx 9 하나
    off = [r for r in fl.rows if r.off_nadir_deg and r.off_nadir_deg > 5]
    assert [r.index for r in off] == [9]
    assert "off_nadir" in off[0].flags
    # AGL 이탈(-50 m) 은 전환 구간 캡처 20
    assert fl.rows[20].agl_m == pytest.approx(70.0, abs=0.01)
    assert "agl_out" in fl.rows[20].flags
    # 궤적 길이는 라인 3개 × 7 step × 19 m + 전환 ≈ 500 m 안팎
    assert s.track_length_m is not None and 450 < s.track_length_m < 560
    assert s.bbox is not None and s.bbox[0] < s.bbox[2] and s.bbox[1] < s.bbox[3]


@pytest.mark.exiftool
def test_m3m_two_flights_and_rtk(report_m3: RunReport) -> None:
    r = report_m3
    assert r.sensor is SensorKind.DJI_M3M
    assert len(r.flights) == 2
    assert [f.summary.n_captures for f in r.flights] == [14, 14]
    assert r.flights[0].summary.utc_offset_min == 540
    rtk = _metric(r, 0, "rtk.fixed_pct")
    assert rtk.status is Status.WARN
    assert rtk.value == pytest.approx(85.7, abs=0.1)
    assert rtk.details["flag_counts"] == {"50": 12, "34": 2}
    assert _metric(r, 1, "rtk.fixed_pct").status is Status.PASS
    assert _metric(r, 0, "integrity.missing_bands").details["by_band"] == {"rgb": 1}
    assert r.flights[0].status is Status.FAIL
    assert r.flights[1].status is Status.PASS
    assert r.status is Status.FAIL
    assert "RelativeAltitude" in r.flights[0].agl_source
    assert r.flights[0].rows[4].rtk_fixed is False and "rtk_not_fixed" in r.flights[0].rows[4].flags
    assert any("[F1_" in a for a in r.next_actions)


@pytest.mark.exiftool
def test_clean_dataset_passes(synth_clean: BuildResult) -> None:
    r = pipeline.run(synth_clean.root, load_config(), use_cache=False)
    assert r.status is Status.PASS, [
        (m.key, m.status, m.reason) for f in r.flights for g in f.groups for m in g.metrics
    ]
    assert r.next_actions == ["추가 조치 없음. Metashape / DJI Terra 에 그대로 투입 가능."]
    assert "후처리 투입 가능" in r.headline and r.action_items == []


@pytest.mark.exiftool
def test_cache_roundtrip(synth_clean: BuildResult, tmp_path: Path) -> None:
    cfg = load_config()
    cfg.cache.path = str(tmp_path / "cache.sqlite")
    r1 = pipeline.run(synth_clean.root, cfg, use_cache=True)
    assert r1.timings_s.get("cache_hits") == 0
    r2 = pipeline.run(synth_clean.root, cfg, use_cache=True)
    assert r2.timings_s.get("cache_hits") == len(synth_clean.files)
    assert r2.status is r1.status
    assert r2.flights[0].summary == r1.flights[0].summary


@pytest.mark.exiftool
def test_progress_and_discovery_callbacks(synth_clean: BuildResult) -> None:
    stages: list[str] = []
    seen: list[int] = []
    pipeline.run(
        synth_clean.root,
        load_config(),
        use_cache=False,
        on_progress=lambda p: stages.append(p.stage),
        on_discovery=lambda d: seen.append(d.n_provisional_captures),
    )
    assert seen == [synth_clean.n_captures]
    assert {"discover", "extract", "analyze", "judge"} <= set(stages)
    assert stages.index("extract") < stages.index("analyze") < stages.index("judge")


@pytest.mark.exiftool
def test_cancel_during_extract(synth_clean: BuildResult) -> None:
    tok = CancelToken()

    def on_progress(p: pipeline.Progress) -> None:
        if p.stage == "extract":
            tok.cancel()

    with pytest.raises(CancelledError):
        pipeline.run(
            synth_clean.root, load_config(), use_cache=False, on_progress=on_progress, cancel=tok
        )


def test_no_sensor_folder(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    r = pipeline.run(tmp_path, load_config(), use_cache=False)
    assert r.status is Status.NA and r.sensor is None and r.flights == []
    assert pipeline.inspect(tmp_path, load_config()).sensor is None


@pytest.mark.exiftool
def test_export_files(report_re: RunReport, tmp_path: Path) -> None:
    written = export.write_all(report_re, tmp_path / "out")
    names = {p.name for p in written}
    assert names == set(export.EXPORT_FILES)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "1.4"
    assert summary["status"] == "FAIL"
    assert summary["flights"][0]["rows"] == []  # 행은 CSV 로
    with (tmp_path / "out" / "captures.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 30
    assert rows[0]["capture_id"] and rows[3]["agl_m"]
    geo = json.loads((tmp_path / "out" / "trajectory.geojson").read_text(encoding="utf-8"))
    kinds = [f["properties"]["kind"] for f in geo["features"]]
    assert kinds.count("track") == 1 and kinds.count("capture") == 30
    with (tmp_path / "out" / "excluded.csv").open(encoding="utf-8-sig") as fh:
        ex = list(csv.DictReader(fh))
    assert len(ex) == 6  # 손상 1 + 중복 5
