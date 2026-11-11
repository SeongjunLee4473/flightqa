"""폴더 스캔, 센서 감지, 캡처 묶기, 비행 분리 (CLAUDE.md §2.3).

- 사용자는 최상위 폴더 하나만 준다. 하위를 재귀 탐색한다.
- 파일명 규칙에 안 맞는 이미지(8.3 이름 등)는 버리지 않고 '미해결' 로 두었다가 태그로 재편입한다.
- 0 바이트·헤더 손상 파일은 exiftool 에 보내기 전에 걸러 제외 목록에 남긴다.
"""

from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

from flightqa.core.metadata import CancelToken, FileEntry
from flightqa.core.models import (
    CaptureMeta,
    DiscoveryResult,
    ExcludedFile,
    Flight,
    SensorKind,
)
from flightqa.core.sensors.base import Raw, SensorPlugin, is_image_file

log = logging.getLogger(__name__)

OUTPUT_DIR_NAME = "flightqa_out"
_SKIP_DIRS = {OUTPUT_DIR_NAME, "__MACOSX", ".git"}
_TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
_JPEG_MAGIC = b"\xff\xd8\xff"

ScanProgress = Callable[[int, str], None]


def scan_files(
    root: Path,
    *,
    follow_symlinks: bool = False,
    cancel: CancelToken | None = None,
    on_progress: ScanProgress | None = None,
) -> tuple[list[FileEntry], list[ExcludedFile]]:
    """이미지 파일을 재귀 수집. 손상 파일은 제외 목록으로.

    ``os.scandir`` 의 ``DirEntry.stat()`` 은 Windows 에서 추가 시스템 호출 없이 크기·mtime 을 주므로
    파일당 ``Path.stat()`` 보다 수십 배 빠르다 (3,550 파일: 0.37 s → 0.01 s 실측).
    """
    entries: list[FileEntry] = []
    excluded: list[ExcludedFile] = []
    n_seen = 0

    def walk(d: Path) -> None:
        nonlocal n_seen
        if cancel is not None:
            cancel.check()
        try:
            with os.scandir(d) as it:
                items = sorted(it, key=lambda e: e.name)
        except OSError as e:
            excluded.append(ExcludedFile(path=d, reason=f"폴더 읽기 실패: {e.strerror}"))
            return
        subdirs: list[Path] = []
        for de in items:
            try:
                is_dir = de.is_dir(follow_symlinks=follow_symlinks)
            except OSError:
                continue
            if is_dir:
                if de.name not in _SKIP_DIRS and not de.name.startswith("."):
                    subdirs.append(Path(de.path))
                continue
            p = Path(de.path)
            if not is_image_file(p):
                continue
            n_seen += 1
            try:
                st = de.stat(follow_symlinks=follow_symlinks)
            except OSError as e:
                excluded.append(ExcludedFile(path=p, reason=f"stat 실패: {e.strerror}"))
                continue
            if st.st_size == 0:
                excluded.append(ExcludedFile(path=p, reason="0 바이트 파일"))
                continue
            reason = _header_problem(p)
            if reason:
                excluded.append(ExcludedFile(path=p, reason=reason))
                continue
            entries.append(FileEntry(path=p, size=st.st_size, mtime_ns=st.st_mtime_ns))
            if on_progress is not None and n_seen % 500 == 0:
                on_progress(n_seen, str(p.parent))
        for sd in subdirs:
            walk(sd)

    walk(root)
    if on_progress is not None:
        on_progress(n_seen, str(root))
    return entries, excluded


def _header_problem(p: Path) -> str | None:
    try:
        with p.open("rb") as fh:
            head = fh.read(4)
    except OSError as e:
        return f"읽기 실패: {e.strerror}"
    if len(head) < 4:
        return "파일이 너무 작음 (헤더 없음)"
    if p.suffix.lower() in {".tif", ".tiff"} and head not in _TIFF_MAGIC:
        return "TIFF 헤더 손상"
    if p.suffix.lower() in {".jpg", ".jpeg"} and not head.startswith(_JPEG_MAGIC):
        return "JPEG 헤더 손상"
    return None


def detect_sensor(
    files: Sequence[Path], root: Path, sensors: Sequence[SensorPlugin]
) -> tuple[SensorPlugin | None, float, dict[str, float]]:
    scores = {s.kind.value: s.detect(files, root) for s in sensors}
    best = max(sensors, key=lambda s: scores[s.kind.value], default=None)
    if best is None or scores[best.kind.value] <= 0.0:
        return None, 0.0, scores
    return best, scores[best.kind.value], scores


def discover(
    root: Path,
    sensors: Sequence[SensorPlugin],
    *,
    follow_symlinks: bool = False,
    cancel: CancelToken | None = None,
    on_progress: ScanProgress | None = None,
) -> tuple[DiscoveryResult, list[FileEntry]]:
    """스캔 + 센서 감지 + 잠정 캡처 수. 두 번째 반환값은 추출 대상 파일."""
    root = root.resolve()
    entries, excluded = scan_files(
        root, follow_symlinks=follow_symlinks, cancel=cancel, on_progress=on_progress
    )
    paths = [e.path for e in entries]
    sensor, conf, scores = detect_sensor(paths, root, sensors)
    result = DiscoveryResult(
        root=root,
        sensor=sensor.kind if sensor else None,
        confidence=conf,
        scores=scores,
        n_files_scanned=len(paths) + len(excluded),
        image_files=paths,
        ignored=excluded,
    )
    if sensor is None:
        return result, []
    keys: set[str] = set()
    groups: Counter[str] = Counter()
    for p in paths:
        prov = sensor.provisional(p, root)
        if prov is None:
            result.unresolved.append(p)
        else:
            keys.add(prov[0])
        groups[sensor.group_of(p, root)] += 1
    result.n_provisional_captures = len(keys)
    result.groups = dict(sorted(groups.items()))
    return result, entries


class RegroupResult:
    """태그 기반 캡처 묶기 결과."""

    def __init__(self) -> None:
        self.captures: dict[str, list[tuple[Path, Raw]]] = defaultdict(list)
        self.groups: dict[str, str] = {}
        self.excluded: list[ExcludedFile] = []
        self.n_duplicates = 0
        self.n_reclaimed = 0
        """파일명 규칙에 안 맞았지만 태그로 편입된 파일 수."""


def regroup(
    sensor: SensorPlugin,
    root: Path,
    records: Iterable[tuple[FileEntry, Raw]],
    unresolved: set[Path] | None = None,
) -> RegroupResult:
    """exiftool 결과를 센서 캡처 키로 묶는다. 실패 파일은 제외 목록에."""
    res = RegroupResult()
    seen_band: dict[tuple[str, str], Path] = {}
    unresolved = unresolved or set()
    for entry, raw in records:
        err = raw.get("ExifTool:Error")
        if err:
            res.excluded.append(ExcludedFile(path=entry.path, reason=f"exiftool: {err}"))
            continue
        key = sensor.capture_key(raw, entry.path, root)
        if key is None:
            res.excluded.append(
                ExcludedFile(path=entry.path, reason="캡처 ID 태그 없음, 파일명 규칙 불일치")
            )
            continue
        band = sensor.band_key(raw, entry.path)
        if band is None:
            res.excluded.append(ExcludedFile(path=entry.path, reason="밴드 식별 불가"))
            continue
        if (key, band) in seen_band:
            res.n_duplicates += 1
            res.excluded.append(
                ExcludedFile(
                    path=entry.path,
                    reason=(
                        f"중복: 캡처 {key} 밴드 {band} 는 "
                        f"{seen_band[(key, band)].name} 가 이미 있음"
                    ),
                )
            )
            continue
        seen_band[(key, band)] = entry.path
        if entry.path in unresolved:
            res.n_reclaimed += 1
        res.captures[key].append((entry.path, raw))
        res.groups.setdefault(key, sensor.group_of(entry.path, root))
    return res


def build_captures(sensor: SensorPlugin, grouped: RegroupResult) -> list[CaptureMeta]:
    metas = [
        sensor.parse(key, grouped.groups.get(key, "."), recs)
        for key, recs in grouped.captures.items()
    ]
    metas.sort(key=_sort_key)
    for i, m in enumerate(metas):
        m.index = i
    return metas


def _sort_key(m: CaptureMeta) -> tuple[int, float, str]:
    if m.time_utc is None:
        return (1, 0.0, m.capture_id)
    return (0, m.time_utc.timestamp(), m.capture_id)


def split_flights(captures: Sequence[CaptureMeta], gap_min: float) -> list[Flight]:
    """시간 공백(gap_min 이상)으로 비행을 나눈다. 시각 없는 캡처는 마지막 'untimed' 비행으로."""
    timed = [c for c in captures if c.time_utc is not None]
    untimed = [c for c in captures if c.time_utc is None]
    flights: list[Flight] = []
    if timed:
        gap = timedelta(minutes=gap_min)
        current: list[CaptureMeta] = [timed[0]]
        for prev, cur in pairwise(timed):
            assert prev.time_utc is not None and cur.time_utc is not None
            if cur.time_utc - prev.time_utc >= gap:
                flights.append(_make_flight(len(flights), current))
                current = []
            current.append(cur)
        flights.append(_make_flight(len(flights), current))
    if untimed:
        flights.append(Flight(flight_id="untimed", index=len(flights), captures=list(untimed)))
    return flights


def _make_flight(index: int, captures: list[CaptureMeta]) -> Flight:
    start = captures[0].time_utc
    fid = f"F{index + 1}" if start is None else f"F{index + 1}_{start:%Y%m%d_%H%M%S}Z"
    return Flight(flight_id=fid, index=index, captures=captures)


def sensor_by_kind(sensors: Sequence[SensorPlugin], kind: SensorKind) -> SensorPlugin:
    for s in sensors:
        if s.kind is kind:
            return s
    raise KeyError(kind)
