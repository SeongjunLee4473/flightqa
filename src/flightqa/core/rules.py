"""판정 엔진 (CLAUDE.md §4.8).

- 개별 항목: 임계값은 설정에서 오고, 여기의 헬퍼는 값→상태 변환만 한다.
- 종합: 게이트 항목이 FAIL 이면 전체 '재비행 검토'. 그 외는 WARN 개수로 등급.
- 모든 판정에 '권고 조치' 문장을 붙인다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from flightqa.core.models import ActionItem, CheckGroup, FlightReport, Metric, Status, worst


def grade_max(value: float | None, warn: float, fail: float) -> Status:
    """값이 클수록 나쁨. value ≥ fail → FAIL, ≥ warn → WARN."""
    if value is None:
        return Status.NA
    if value >= fail:
        return Status.FAIL
    if value >= warn:
        return Status.WARN
    return Status.PASS


def grade_min(value: float | None, warn: float, fail: float) -> Status:
    """값이 작을수록 나쁨. value < fail → FAIL, < warn → WARN (경계값은 통과: "75% 이상")."""
    if value is None:
        return Status.NA
    if value < fail:
        return Status.FAIL
    if value < warn:
        return Status.WARN
    return Status.PASS


def grade_deviation_pct(
    value: float | None, target: float, warn_pct: float, fail_pct: float
) -> tuple[Status, float | None]:
    """목표값 대비 편차(%) 로 판정. (상태, 편차%) 반환."""
    if value is None or target == 0:
        return Status.NA, None
    dev = abs(value - target) / abs(target) * 100.0
    return grade_max(dev, warn_pct, fail_pct), dev


def fmt(v: float | None, digits: int = 1, unit: str = "") -> str:
    if v is None:
        return "—"
    s = f"{v:,.{digits}f}"
    return f"{s} {unit}".strip()


def info_metric(
    key: str, label: str, value: float | int | str | None, unit: str | None, reason: str
) -> Metric:
    """기준 없는 정보 항목. 값이 없으면 N/A."""
    return Metric(
        key=key,
        label=label,
        value=value,
        unit=unit,
        threshold=None,
        status=Status.PASS if value is not None else Status.NA,
        reason=reason,
    )


# --------------------------------------------------------------------------- 종합


def flight_status(groups: Sequence[CheckGroup], warn_to_fail_count: int) -> Status:
    metrics = [m for g in groups for m in g.metrics]
    if any(m.status is Status.FAIL and m.gate for m in metrics):
        return Status.FAIL
    n_warn = sum(1 for m in metrics if m.status is Status.WARN)
    n_fail = sum(1 for m in metrics if m.status is Status.FAIL)
    if n_fail:
        return Status.FAIL
    if n_warn >= warn_to_fail_count:
        return Status.FAIL
    return worst([m.status for m in metrics])


class Verdict(NamedTuple):
    status: Status
    headline: str
    """짧은 결론."""
    detail: str
    """결론의 근거 한 줄."""
    actions: list[str]
    """권고 조치 텍스트 (CLI·호환). 비행 항목은 '[flight_id] ...' 접두."""
    items: list[ActionItem]
    """권고 조치 구조 (FAIL 먼저, 그다음 WARN; 각각 원래 순서)."""


def judge_run(flights: Sequence[FlightReport], run_groups: Sequence[CheckGroup]) -> Verdict:
    """전체 상태, 짧은 헤드라인, 근거 한 줄, 권고 조치."""
    statuses = [f.status for f in flights] + [g.status for g in run_groups]
    overall = worst(statuses) if statuses else Status.NA
    gate_fail = [
        (f, m)
        for f in flights
        for g in f.groups
        for m in g.metrics
        if m.status is Status.FAIL and m.gate
    ]
    run_fail = [m for g in run_groups for m in g.metrics if m.status is Status.FAIL]
    if run_fail and overall is not Status.FAIL:
        overall = Status.FAIL

    items: list[ActionItem] = []
    n_fail = n_warn = 0
    for i, f in enumerate(flights):
        for g in f.groups:
            for m in g.metrics:
                n_fail += m.status is Status.FAIL
                n_warn += m.status is Status.WARN
                if m.status in (Status.FAIL, Status.WARN) and m.next_action:
                    items.append(
                        ActionItem(
                            flight_id=f.summary.flight_id,
                            flight_index=i,
                            group_key=g.key,
                            group_label=g.label,
                            metric_key=m.key,
                            metric_label=m.label,
                            status=m.status,
                            text=m.next_action,
                        )
                    )
    for g in run_groups:
        for m in g.metrics:
            n_fail += m.status is Status.FAIL
            n_warn += m.status is Status.WARN
            if m.status in (Status.FAIL, Status.WARN) and m.next_action:
                items.append(
                    ActionItem(
                        group_key=g.key,
                        group_label=g.label,
                        metric_key=m.key,
                        metric_label=m.label,
                        status=m.status,
                        text=m.next_action,
                    )
                )
    items.sort(key=lambda it: 0 if it.status is Status.FAIL else 1)  # 안정 정렬: FAIL 먼저
    actions = [f"[{it.flight_id}] {it.text}" if it.flight_id else it.text for it in items]

    n_flights = len(flights)
    if overall is Status.FAIL:
        headline = "재비행 검토 필요"
        if gate_fail:
            keys = sorted({m.label for _, m in gate_fail})
            detail = f"게이트 항목 실패 {len(keys)}건: {', '.join(keys)}"
        else:
            detail = f"실패 {n_fail}건 · 경고 {n_warn}건"
    elif overall is Status.WARN:
        headline = "조건부 사용 가능"
        detail = f"비행 {n_flights}개 · 경고 {n_warn}건 · 아래 권고 조치 확인"
    elif overall is Status.PASS:
        headline = "후처리 투입 가능"
        detail = f"비행 {n_flights}개 모두 기준 충족"
    else:
        headline = "판정 불가"
        detail = "분석할 캡처가 없음"
    if not actions and overall is Status.PASS:
        actions.append("추가 조치 없음. Metashape / DJI Terra 에 그대로 투입 가능.")
    return Verdict(overall, headline, detail, actions, items)
