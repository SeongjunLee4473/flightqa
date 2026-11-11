"""exiftool 배치 메타데이터 추출.

- 워커 스레드마다 ``-stay_open`` exiftool 프로세스 1 개 (pyexiftool). 파일당 프로세스 금지.
- 단일 프로세스는 초당 ~70 파일 (실측 2026-09-10) → CPU 코어 수만큼 병렬화. 4 코어 ≈ 270 파일/s.
- 출력은 ``-G1 -n -struct -a`` 원본 태그 dict. 메모리 상한을 위해 필요한 그룹만 남긴다.
- 비-ASCII 경로: ``-charset filename=utf8`` 과 UTF-8 stdin 조합 (docs/ASSUMPTIONS.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from exiftool import ExifToolHelper

from flightqa.core.exiftool import find_exiftool
from flightqa.core.sensors.base import Raw

log = logging.getLogger(__name__)

COMMON_ARGS = ["-G1", "-n", "-struct", "-a", "-charset", "filename=utf8"]
KEEP_PREFIXES = (
    "XMP-",
    "ExifIFD:",
    "GPS:",
    "IFD0:",
    "Composite:GPS",
    "System:",
    "File:",
    "ExifTool:",
    "SourceFile",
)
DROP_TAGS = frozenset(
    {
        "IFD0:StripOffsets",
        "IFD0:StripByteCounts",
        "IFD0:OpcodeList3",
        "ExifIFD:ThumbnailImage",
        "ExifIFD:DeviceSettingDescription",
        "XMP-x:XMPToolkit",
    }
)


class CancelledError(Exception):
    """사용자 취소."""


class CancelToken:
    """모든 루프가 확인하는 취소 플래그 (CLAUDE.md §7)."""

    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    @property
    def is_cancelled(self) -> bool:
        return self._ev.is_set()

    def check(self) -> None:
        if self._ev.is_set():
            raise CancelledError()


@dataclass(frozen=True, slots=True)
class FileEntry:
    """스캔한 파일. 캐시 키는 경로+크기+mtime (CLAUDE.md §7)."""

    path: Path
    size: int
    mtime_ns: int

    @property
    def key(self) -> str:
        h = hashlib.sha1(f"{self.path}|{self.size}|{self.mtime_ns}".encode())
        return h.hexdigest()

    @classmethod
    def from_path(cls, path: Path) -> FileEntry:
        st = path.stat()
        return cls(path=path, size=st.st_size, mtime_ns=st.st_mtime_ns)


def slim_raw(raw: Raw) -> Raw:
    """파서에 필요한 그룹만 남긴다. 바이너리 자리표시자와 큰 배열은 버린다."""
    out: Raw = {}
    for k, v in raw.items():
        if k in DROP_TAGS or not k.startswith(KEEP_PREFIXES):
            continue
        if isinstance(v, str) and v.startswith("(Binary data"):
            continue
        out[k] = v
    return out


ProgressFn = Callable[[int, int, str], None]


class ExifToolPool:
    """스레드별 exiftool 프로세스 풀. ``with`` 로 쓰면 종료 시 프로세스를 정리한다."""

    def __init__(
        self,
        exe: Path | None = None,
        *,
        workers: int | None = None,
        chunk_size: int = 50,
    ) -> None:
        self.exe = exe or find_exiftool()
        self.workers = workers or max(1, min(os.cpu_count() or 2, 8))
        self.chunk_size = max(1, chunk_size)
        self._local = threading.local()
        self._helpers: list[ExifToolHelper] = []
        self._lock = threading.Lock()

    def __enter__(self) -> ExifToolPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            for h in self._helpers:
                try:
                    h.terminate()
                except Exception as e:  # noqa: BLE001 - 종료 실패는 치명적이지 않음, 기록만
                    log.debug("exiftool terminate 실패: %s", e)
            self._helpers.clear()

    def _helper(self) -> ExifToolHelper:
        h: ExifToolHelper | None = getattr(self._local, "helper", None)
        if h is None:
            h = ExifToolHelper(
                executable=str(self.exe),
                common_args=COMMON_ARGS,
                encoding="utf-8",
                check_execute=False,
                check_tag_names=False,
            )
            h.run()
            self._local.helper = h
            with self._lock:
                self._helpers.append(h)
        return h

    def _run_chunk(self, files: Sequence[Path]) -> list[Raw]:
        helper = self._helper()
        try:
            out: list[Raw] = helper.execute_json(*(str(p) for p in files))
        except Exception as e:  # noqa: BLE001 - 청크 실패 시 파일 단위로 재시도해 원인을 파일에 귀속
            log.warning("exiftool 청크 실패(%d 파일), 개별 재시도: %s", len(files), e)
            out = []
            for p in files:
                try:
                    out.extend(helper.execute_json(str(p)))
                except Exception as e2:  # noqa: BLE001 - 개별 파일 오류는 결과에 기록하고 계속
                    out.append(
                        {"SourceFile": str(p), "ExifTool:Error": f"{type(e2).__name__}: {e2}"}
                    )
        return [slim_raw(r) for r in out]

    def extract(
        self,
        entries: Sequence[FileEntry],
        *,
        on_progress: ProgressFn | None = None,
        cancel: CancelToken | None = None,
    ) -> Iterator[tuple[FileEntry, Raw]]:
        """파일들을 청크로 나눠 병렬 추출. 완료 순서대로 (entry, raw) 를 낸다."""
        if not entries:
            return
        by_source = {_norm(e.path): e for e in entries}
        chunks = [entries[i : i + self.chunk_size] for i in range(0, len(entries), self.chunk_size)]
        done = 0
        total = len(entries)
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="exiftool") as ex:
            futures = {ex.submit(self._run_chunk, [e.path for e in c]): c for c in chunks}
            try:
                for fut in as_completed(futures):
                    if cancel is not None and cancel.is_cancelled:
                        for f in futures:
                            f.cancel()
                        raise CancelledError()
                    chunk = futures[fut]
                    results = fut.result()
                    seen: set[str] = set()
                    for raw in results:
                        src = _norm(Path(str(raw.get("SourceFile", ""))))
                        entry = by_source.get(src)
                        if entry is None:
                            log.debug("exiftool 결과의 SourceFile 을 매칭하지 못함: %s", src)
                            continue
                        seen.add(src)
                        yield entry, raw
                    for e in chunk:  # exiftool 이 아예 출력하지 않은 파일
                        if _norm(e.path) not in seen:
                            yield (
                                e,
                                {"SourceFile": str(e.path), "ExifTool:Error": "exiftool 출력 없음"},
                            )
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total, chunk[-1].path.name)
            finally:
                if cancel is not None and cancel.is_cancelled:
                    ex.shutdown(wait=False, cancel_futures=True)


def _norm(p: Path) -> str:
    return os.path.normcase(str(p if p.is_absolute() else p.resolve()))


def exiftool_version_of(exe: Path | None = None) -> str | None:
    from flightqa.core.exiftool import exiftool_version

    try:
        return exiftool_version(exe or find_exiftool())
    except Exception as e:  # noqa: BLE001 - 버전 표시는 부가 정보
        log.debug("exiftool 버전 조회 실패: %s", e)
        return None


def raw_to_json(raw: Raw) -> bytes:
    return json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def json_to_raw(blob: bytes) -> Raw:
    data: Raw = json.loads(blob.decode("utf-8"))
    return data


def iter_slim(records: Iterable[dict[str, Any]]) -> Iterator[Raw]:
    for r in records:
        yield slim_raw(r)
