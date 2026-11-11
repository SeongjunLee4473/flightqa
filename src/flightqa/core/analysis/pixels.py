"""픽셀 통계 (§4.5 포화, §4.7 패널 힌트).

파이프라인에서 유일하게 픽셀 I/O 를 하는 곳 (CLAUDE.md §7).

- ``tifffile`` 로 밴드 파일을 통째로 읽고 numpy 벡터 연산으로 포화 비율·백분위·히스토그램을 낸다.
- ``stride`` > 1 이면 ``img[::s, ::s]`` 로 서브샘플링 (초고속 모드). 다운샘플 보간은 하지 않는다.
- ``ProcessPoolExecutor`` 로 병렬화. Windows spawn 을 위해 작업 함수는 모듈 최상위이고 인자는
  pickle 가능.
- 파일 하나가 깨져도 멈추지 않는다: 결과에 ``error`` 를 넣고 계속한다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

from flightqa.core.metadata import CancelledError, CancelToken

log = logging.getLogger(__name__)

HIST_RANGE = (0, 65536)


@dataclass(frozen=True, slots=True)
class PixelTask:
    key: str
    """캐시 키 (파일 키 + 파라미터)."""
    path: str
    sat_dn: int
    stride: int = 1
    bins: int = 64
    bright_region: bool = False


def pixel_cache_key(file_key: str, sat_dn: int, stride: int, bins: int, bright: bool) -> str:
    return f"{file_key}|{sat_dn}|{stride}|{bins}|{int(bright)}"


def compute_pixel_stats(task: PixelTask) -> dict[str, Any]:
    """밴드 파일 1 개의 통계. 예외는 잡아서 ``error`` 로 돌려준다 (풀에서 조용히 죽지 않도록)."""
    try:
        img = tifffile.imread(task.path)
    except Exception as e:  # noqa: BLE001 - 파일 손상은 결과로 보고
        return {"error": f"{type(e).__name__}: {e}"}
    if img.ndim > 2:
        img = img.reshape(img.shape[0], img.shape[1], -1)[:, :, 0]
    if task.stride > 1:
        img = img[:: task.stride, :: task.stride]
    flat = np.ascontiguousarray(img).ravel()
    n = int(flat.size)
    if n == 0:
        return {"error": "빈 이미지"}
    sat = int(np.count_nonzero(flat >= task.sat_dn))
    p1, p50, p99 = (float(v) for v in np.percentile(flat, [1, 50, 99]))
    hist, _ = np.histogram(flat, bins=task.bins, range=HIST_RANGE)
    out: dict[str, Any] = {
        "n": n,
        "sat_pct": sat / n * 100.0,
        "p1": p1,
        "p50": p50,
        "p99": p99,
        "mean": float(flat.mean()),
        "max": int(flat.max()),
        "hist": hist.astype(int).tolist(),
    }
    if task.bright_region:
        out.update(_bright_region(img, p50, p99))
    return out


def _bright_region(img: np.ndarray[Any, Any], p50: float, p99: float) -> dict[str, float]:
    """밝고 균일한 사각 영역(캘리브레이션 패널) 힌트.

    임계값 = p50 과 p99 의 중간. 그 위 픽셀의 비율, 경계상자 채움률, 영역 내 변동계수를 돌려준다.
    패널이면 비율이 수 %–수십 %, 채움률이 높고(직사각형), 변동계수가 작다. 정밀 검출은 비범위.
    """
    thr = (p50 + p99) / 2.0
    mask = img >= thr
    cnt = int(mask.sum())
    if cnt == 0:
        return {"bright_frac": 0.0, "bright_fill": 0.0, "bright_cv": 1.0}
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    box = (rows[-1] - rows[0] + 1) * (cols[-1] - cols[0] + 1)
    vals = img[mask].astype(np.float64)
    cv = float(vals.std() / vals.mean()) if vals.mean() > 0 else 1.0
    return {"bright_frac": cnt / img.size, "bright_fill": cnt / box, "bright_cv": cv}


ProgressFn = Callable[[int, int, str], None]


def run_pixel_tasks(
    tasks: Sequence[PixelTask],
    *,
    workers: int | None = None,
    on_progress: ProgressFn | None = None,
    cancel: CancelToken | None = None,
    chunk: int = 20,
) -> Iterator[tuple[PixelTask, dict[str, Any]]]:
    """작업을 프로세스 풀에 청크로 나눠 던지고 완료 순서대로 낸다. 취소 시 즉시 종료."""
    if not tasks:
        return
    workers = workers or max(1, min(os.cpu_count() or 2, 8))
    if workers == 1 or len(tasks) <= chunk:
        for i, t in enumerate(tasks, 1):
            if cancel is not None and cancel.is_cancelled:
                raise CancelledError()
            yield t, compute_pixel_stats(t)
            if on_progress is not None and (i % chunk == 0 or i == len(tasks)):
                on_progress(i, len(tasks), Path(t.path).name)
        return
    chunks = [tasks[i : i + chunk] for i in range(0, len(tasks), chunk)]
    done = 0
    finished: set[int] = set()
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_run_chunk, c): k for k, c in enumerate(chunks)}
            try:
                for fut in as_completed(futures):
                    if cancel is not None and cancel.is_cancelled:
                        raise CancelledError()
                    k = futures[fut]
                    c = chunks[k]
                    results = fut.result()
                    finished.add(k)
                    yield from zip(c, results, strict=True)
                    done += len(c)
                    if on_progress is not None:
                        on_progress(done, len(tasks), Path(c[-1].path).name)
            finally:
                if cancel is not None and cancel.is_cancelled:
                    ex.shutdown(wait=False, cancel_futures=True)
    except BrokenProcessPool as e:
        # 얼린 exe·비정상 환경에서 워커가 못 뜨는 경우: 조용히 죽지 말고 순차 처리로 이어간다.
        log.warning(
            "프로세스 풀 실패, 남은 %d 청크를 순차 처리: %s", len(chunks) - len(finished), e
        )
        for k, c in enumerate(chunks):
            if k in finished:
                continue
            if cancel is not None and cancel.is_cancelled:
                raise CancelledError() from None
            for t in c:
                yield t, compute_pixel_stats(t)
            done += len(c)
            if on_progress is not None:
                on_progress(done, len(tasks), Path(c[-1].path).name)


def _run_chunk(tasks: Sequence[PixelTask]) -> list[dict[str, Any]]:
    return [compute_pixel_stats(t) for t in tasks]
