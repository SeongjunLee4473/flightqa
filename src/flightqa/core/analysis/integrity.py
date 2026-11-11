"""§4.2 캡처 무결성.

비행 단위: 밴드 누락, 캡처 간격·공백, 촬영 시각 역행, 파일 mtime–EXIF 불일치.
데이터셋 단위: 손상 파일, 중복 캡처, 미편입 파일 (``analyze_files``).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime, timedelta

from flightqa.config.schema import IntegrityConfig
from flightqa.core.models import (
    CaptureMeta,
    CheckGroup,
    DiscoveryResult,
    ExcludedFile,
    Metric,
    Status,
)
from flightqa.core.rules import fmt, grade_max, info_metric
from flightqa.core.sensors.base import SensorPlugin


def intervals_s(captures: Sequence[CaptureMeta]) -> list[float | None]:
    """캡처 i 의 직전 캡처와의 간격(초). 첫 캡처와 시각 없는 캡처는 None."""
    out: list[float | None] = [None] * len(captures)
    for i in range(1, len(captures)):
        a, b = captures[i - 1].time_utc, captures[i].time_utc
        out[i] = (b - a).total_seconds() if a is not None and b is not None else None
    return out


def analyze_integrity(
    captures: Sequence[CaptureMeta],
    sensor: SensorPlugin,
    cfg: IntegrityConfig,
    utc_offset_min: int | None,
) -> tuple[CheckGroup, list[float | None], list[int]]:
    """(그룹, 간격 리스트, 공백이 시작되는 캡처 인덱스) 반환."""
    n = len(captures)
    metrics: list[Metric] = []
    ivals = intervals_s(captures)
    gap_idx: list[int] = []

    # 1. 밴드 누락
    incomplete = [c for c in captures if c.missing_bands]
    pct = (len(incomplete) / n * 100.0) if n else None
    st = grade_max(pct, cfg.missing_band_warn_pct, cfg.missing_band_fail_pct)
    missing_counts: dict[str, int] = {}
    for c in incomplete:
        for b in c.missing_bands:
            missing_counts[b] = missing_counts.get(b, 0) + 1
    metrics.append(
        Metric(
            key="integrity.missing_bands",
            label="밴드 누락 캡처",
            value=round(pct, 2) if pct is not None else None,
            unit="%",
            threshold=f"WARN ≥ {cfg.missing_band_warn_pct}%, FAIL ≥ {cfg.missing_band_fail_pct}%",
            status=st,
            reason=(
                f"{len(incomplete)}/{n} 캡처에 밴드 파일이 빠짐"
                + (f" (밴드별: {missing_counts})" if missing_counts else "")
                if n
                else "캡처 없음"
            ),
            next_action=(
                "해당 위치의 중복도 확인 · 누락이 특정 라인에 몰리면 그 라인만 재비행\n"
                "영향: 누락 캡처는 후처리에서 자동 제외"
                if st in (Status.WARN, Status.FAIL)
                else None
            ),
            details={
                "n_incomplete": len(incomplete),
                "by_band": missing_counts,
                "capture_ids": [c.capture_id for c in incomplete[:50]],
            },
            gate=True,
        )
    )

    # 2. 캡처 간격·공백
    valid = [v for v in ivals if v is not None and v > 0]
    median = statistics.median(valid) if valid else None
    if median is not None:
        thr = median * cfg.interval_outlier_factor
        gap_idx = [i for i, v in enumerate(ivals) if v is not None and v > thr]
        p95 = _percentile(valid, 95)
        metrics.append(
            info_metric(
                "integrity.interval_median",
                "캡처 간격 (중앙값)",
                round(median, 2),
                "s",
                f"p95 {fmt(p95, 2, 's')}, 최대 {fmt(max(valid), 2, 's')}, n={len(valid)}",
            )
        )
        st = grade_max(float(len(gap_idx)), float(cfg.gap_warn_count), float(cfg.gap_fail_count))
        gaps = [
            {
                "index": i,
                "capture_id": captures[i].capture_id,
                "gap_s": round(ivals[i] or 0.0, 1),
                "time_utc": _iso(captures[i].time_utc),
            }
            for i in gap_idx[:100]
        ]
        metrics.append(
            Metric(
                key="integrity.gaps",
                label="캡처 공백 (누락 의심 구간)",
                value=len(gap_idx),
                unit="개",
                threshold=(
                    f"간격 > 중앙값×{cfg.interval_outlier_factor} ; "
                    f"WARN ≥ {cfg.gap_warn_count}개, FAIL ≥ {cfg.gap_fail_count}개"
                ),
                status=st,
                reason=(
                    f"중앙 간격 {median:.2f}s 의 {cfg.interval_outlier_factor}배({thr:.1f}s) 를 "
                    f"넘는 공백 {len(gap_idx)}곳"
                    + (f", 최대 {max(ivals[i] or 0 for i in gap_idx):.0f}s" if gap_idx else "")
                ),
                next_action=(
                    "공백 구간 궤적 위치의 종중복도 확인 · "
                    "긴 공백(수십 초 이상)은 해당 구간 재비행 검토"
                    if st is not Status.PASS
                    else None
                ),
                details={"threshold_s": round(thr, 2), "gaps": gaps},
            )
        )
    else:
        metrics.append(
            Metric(
                key="integrity.gaps",
                label="캡처 공백 (누락 의심 구간)",
                status=Status.NA,
                reason="시각 정보가 있는 캡처가 2 개 미만",
            )
        )

    # 3. 촬영 시각 역행
    reversals = [i for i, v in enumerate(ivals) if v is not None and v < 0]
    st = grade_max(float(len(reversals)), float(cfg.clock_reversal_warn_count), float("inf"))
    metrics.append(
        Metric(
            key="integrity.clock_reversal",
            label="촬영 시각 역행",
            value=len(reversals),
            unit="회",
            threshold=f"WARN ≥ {cfg.clock_reversal_warn_count}회",
            status=st,
            reason=(
                "촬영 시각이 직전 캡처보다 앞선 경우 없음"
                if not reversals
                else f"{len(reversals)}회 역행 (정렬은 시각 기준이라 파일명 순서와 다를 수 있음)"
            ),
            next_action=(
                "캡처 ID 순서와 시각 순서 대조 · 후처리에서 시각 기반 정렬 해제 검토\n"
                "원인: GPS 시각 동기화 실패 가능성"
                if reversals
                else None
            ),
            details={"indices": reversals[:50]},
        )
    )

    # 4. 파일 시각 vs EXIF 시각
    mism = 0
    checked = 0
    offsets = [0.0, float(utc_offset_min * 60)] if utc_offset_min is not None else [0.0]
    examples: list[dict[str, str | float]] = []
    for c in captures:
        for bm in c.bands.values():
            if bm.mtime_utc is None or bm.time_utc is None:
                continue
            checked += 1
            delta = (bm.mtime_utc - bm.time_utc).total_seconds()
            # RedEdge 는 UTC 숫자를 로컬 FAT 시각에 그대로 쓰므로 offset 만큼 어긋나 보일 수 있다
            best = min(abs(delta - off) for off in offsets + [-o for o in offsets])
            if best > cfg.time_mismatch_tolerance_s:
                mism += 1
                if len(examples) < 20:
                    examples.append({"file": bm.path.name, "delta_s": round(delta, 1)})
    pct = (mism / checked * 100.0) if checked else None
    st = grade_max(pct, cfg.time_mismatch_warn_pct, float("inf"))
    metrics.append(
        Metric(
            key="integrity.time_mismatch",
            label="파일 수정 시각–촬영 시각 불일치",
            value=round(pct, 2) if pct is not None else None,
            unit="%",
            threshold=(
                f"|Δ| > {cfg.time_mismatch_tolerance_s:.0f}s ; WARN ≥ {cfg.time_mismatch_warn_pct}%"
            ),
            status=st,
            reason=(
                f"{mism}/{checked} 파일 (센서별 UTC/로컬 관례 보정 후)"
                if checked
                else "비교할 시각 정보 없음"
            ),
            next_action=(
                "원본 카드에서 직접 복사했는지 확인\n원인: 복사·편집 과정에서 파일 갱신 가능성"
                if st is Status.WARN
                else None
            ),
            details={"examples": examples},
        )
    )

    # 5. 촬영 트리거 / 캡처 수 정보
    n_complete = n - len(incomplete)
    metrics.append(
        info_metric(
            "integrity.n_captures",
            "캡처 수",
            n,
            "회",
            f"밴드 누락 없음 {n_complete}건, 누락 {len(incomplete)}건 "
            f"(밴드 {len(sensor.band_keys)}개 기준)",
        )
    )
    return CheckGroup(key="integrity", label="캡처 무결성", metrics=metrics), ivals, gap_idx


def analyze_files(
    discovery: DiscoveryResult,
    excluded: Sequence[ExcludedFile],
    n_duplicates: int,
    n_reclaimed: int,
    cfg: IntegrityConfig,
) -> CheckGroup:
    """데이터셋 단위 파일 문제."""
    metrics: list[Metric] = []
    total = max(discovery.n_files_scanned, 1)
    corrupt = [
        e
        for e in list(discovery.ignored) + list(excluded)
        if "중복" not in e.reason and "캡처 ID" not in e.reason and "밴드 식별" not in e.reason
    ]
    pct = len(corrupt) / total * 100.0
    st = Status.PASS
    if len(corrupt) >= cfg.corrupt_file_warn_count:
        st = Status.WARN
    if pct >= cfg.corrupt_file_fail_pct:
        st = Status.FAIL
    metrics.append(
        Metric(
            key="files.corrupt",
            label="손상·읽기 실패 파일",
            value=len(corrupt),
            unit="개",
            threshold=(
                f"WARN ≥ {cfg.corrupt_file_warn_count}개, FAIL ≥ {cfg.corrupt_file_fail_pct}% "
                f"(전체 {discovery.n_files_scanned})"
            ),
            status=st,
            reason=(
                "없음"
                if not corrupt
                else f"{len(corrupt)}개 ({pct:.2f}%): " + "; ".join(e.reason for e in corrupt[:3])
            ),
            next_action=(
                "원본 카드에서 재복사 후 재분석 · 카드 자체 오류면 카드 교체\n"
                "영향: 손상 파일은 분석에서 제외"
                if corrupt
                else None
            ),
            details={"files": [str(e.path) for e in corrupt[:100]]},
        )
    )
    st = Status.WARN if n_duplicates >= cfg.duplicate_warn_count else Status.PASS
    metrics.append(
        Metric(
            key="files.duplicates",
            label="중복 캡처 파일",
            value=n_duplicates,
            unit="개",
            threshold=f"WARN ≥ {cfg.duplicate_warn_count}개",
            status=st,
            reason="같은 캡처 ID·밴드 조합이 두 파일 이상" if n_duplicates else "없음",
            next_action=(
                "제외 목록의 파일 확인 후 후처리 입력에서 제거\n원인: 복사본 혼입 가능성"
                if n_duplicates
                else None
            ),
        )
    )
    unresolved_left = len(discovery.unresolved) - n_reclaimed
    metrics.append(
        info_metric(
            "files.unresolved",
            "파일명 규칙 불일치 이미지",
            len(discovery.unresolved),
            "개",
            f"태그로 캡처에 편입 {n_reclaimed}개, 편입 실패 {max(unresolved_left, 0)}개",
        )
    )
    return CheckGroup(key="files", label="파일 상태", metrics=metrics)


def _percentile(values: Sequence[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return float("nan")
    k = (len(s) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def duration_of(captures: Sequence[CaptureMeta]) -> timedelta | None:
    times = [c.time_utc for c in captures if c.time_utc is not None]
    if len(times) < 2:
        return None
    return max(times) - min(times)
