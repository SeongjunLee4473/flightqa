from __future__ import annotations

from datetime import UTC, datetime

from flightqa.core.models import (
    CheckGroup,
    FlightReport,
    FlightSummary,
    Metric,
    Status,
    worst,
)
from flightqa.core.rules import (
    flight_status,
    fmt,
    grade_deviation_pct,
    grade_max,
    grade_min,
    info_metric,
    judge_run,
)


def test_grades() -> None:
    assert grade_max(None, 1, 2) is Status.NA
    assert grade_max(0.5, 1, 2) is Status.PASS
    assert grade_max(1.0, 1, 2) is Status.WARN
    assert grade_max(2.0, 1, 2) is Status.FAIL
    assert grade_min(96, 95, 80) is Status.PASS
    assert grade_min(95, 95, 80) is Status.PASS  # 경계값은 통과
    assert grade_min(94.9, 95, 80) is Status.WARN
    assert grade_min(80, 95, 80) is Status.WARN
    assert grade_min(79.9, 95, 80) is Status.FAIL
    st, dev = grade_deviation_pct(100, 120, 20, 30)
    assert st is Status.PASS and dev is not None and abs(dev - 16.67) < 0.01
    assert grade_deviation_pct(90, 120, 20, 30)[0] is Status.WARN
    assert grade_deviation_pct(80, 120, 20, 30)[0] is Status.FAIL
    assert grade_deviation_pct(None, 120, 20, 30) == (Status.NA, None)
    assert grade_deviation_pct(5, 0, 20, 30) == (Status.NA, None)


def test_worst_and_fmt() -> None:
    assert worst([]) is Status.NA
    assert worst([Status.NA, Status.NA]) is Status.NA
    assert worst([Status.NA, Status.PASS]) is Status.PASS
    assert worst([Status.PASS, Status.WARN, Status.NA]) is Status.WARN
    assert worst([Status.WARN, Status.FAIL]) is Status.FAIL
    assert fmt(None) == "—"
    assert fmt(1234.5678, 2, "m") == "1,234.57 m"
    assert info_metric("k", "l", None, None, "r").status is Status.NA
    assert info_metric("k", "l", 3, "개", "r").status is Status.PASS


def _flight(metrics: list[Metric], status: Status) -> FlightReport:
    summary = FlightSummary(
        flight_id="F1",
        index=0,
        n_captures=1,
        n_complete=1,
        start_utc=datetime(2026, 1, 1, tzinfo=UTC),
        end_utc=None,
        duration_s=None,
        utc_offset_min=540,
        n_lines=0,
        mean_agl_m=None,
        median_interval_s=None,
        median_speed_mps=None,
        track_length_m=None,
    )
    return FlightReport(
        summary=summary,
        status=status,
        groups=[CheckGroup(key="g", label="g", metrics=metrics)],
        lines=[],
        rows=[],
    )


def _m(key: str, status: Status, gate: bool = False, action: str | None = "act") -> Metric:
    return Metric(key=key, label=key, status=status, gate=gate, next_action=action)


def test_flight_status_gates_and_warn_accumulation() -> None:
    assert (
        flight_status([CheckGroup(key="g", label="g", metrics=[_m("a", Status.PASS)])], 6)
        is Status.PASS
    )
    warns = [_m(f"w{i}", Status.WARN) for i in range(6)]
    assert flight_status([CheckGroup(key="g", label="g", metrics=warns)], 6) is Status.FAIL
    assert flight_status([CheckGroup(key="g", label="g", metrics=warns[:5])], 6) is Status.WARN
    assert (
        flight_status(
            [CheckGroup(key="g", label="g", metrics=[_m("f", Status.FAIL, gate=True)])], 6
        )
        is Status.FAIL
    )
    assert flight_status([CheckGroup(key="g", label="g", metrics=[])], 6) is Status.NA


def test_judge_run_headlines() -> None:
    ok = _flight([_m("a", Status.PASS, action=None)], Status.PASS)
    v = judge_run([ok], [])
    assert v.status is Status.PASS and "투입 가능" in v.headline and "1개" in v.detail
    assert v.actions == ["추가 조치 없음. Metashape / DJI Terra 에 그대로 투입 가능."]
    assert v.items == []

    warn = _flight([_m("w", Status.WARN)], Status.WARN)
    v = judge_run([warn], [])
    assert v.status is Status.WARN and "조건부" in v.headline and v.actions == ["[F1] act"]
    assert v.items[0].flight_id == "F1" and v.items[0].status is Status.WARN
    assert v.items[0].text == "act" and v.items[0].metric_key == "w"

    gate = _flight([_m("g", Status.FAIL, gate=True)], Status.FAIL)
    v = judge_run([gate], [])
    assert v.status is Status.FAIL and "재비행" in v.headline
    assert "게이트" in v.detail and "g" in v.detail and "—" not in v.headline

    run_fail = CheckGroup(key="files", label="files", metrics=[_m("files.x", Status.FAIL)])
    v = judge_run([ok], [run_fail])
    assert v.status is Status.FAIL and v.actions == ["act"] and v.items[0].flight_id is None

    # FAIL 항목이 WARN 보다 앞에 온다 (원래 순서는 유지)
    mixed = _flight(
        [_m("w1", Status.WARN), _m("f1", Status.FAIL), _m("w2", Status.WARN)], Status.FAIL
    )
    v = judge_run([mixed], [])
    assert [it.metric_key for it in v.items] == ["f1", "w1", "w2"]

    v = judge_run([], [])
    assert v.status is Status.NA and "판정 불가" in v.headline
