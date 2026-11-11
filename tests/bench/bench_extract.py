"""벤치마크 (CLAUDE.md §7): 3,000 캡처 × 5 밴드 = 15,000 파일에서 메타데이터 추출 ≤ 60 s.

합성 데이터는 64×48 소형 TIFF 라 픽셀 I/O 는 대표하지 않는다 (exiftool 은 헤더·XMP 만 읽으므로
파일당 고정 비용이 지배적이다). 실제 폴더로도 잴 수 있다:

    uv run python tests/bench/bench_extract.py                  # 합성 3,000 캡처 (최초 생성 수 분)
    uv run python tests/bench/bench_extract.py --captures 500   # 작은 규모
    uv run python tests/bench/bench_extract.py --real <folder>  # 실제 폴더 (결과는 저장소 밖)

결과는 실측값만 README 에 기록한다. 추정치는 '추정' 이라고 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tests"))

from fixtures.synth import FlightSpec, build_dataset  # noqa: E402
from flightqa.config.schema import load_config  # noqa: E402
from flightqa.core import pipeline  # noqa: E402
from flightqa.core.exiftool import find_exiftool  # noqa: E402


@dataclass
class BenchResult:
    captures: int
    files: int
    workers: int
    cache: str
    discover_s: float
    extract_s: float
    analyze_s: float
    total_s: float
    files_per_s: float
    status: str


def synth_root(captures: int) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "flightqa" / "bench"
    root = base / f"rededge_{captures}"
    marker = root / "DONE"
    if marker.exists():
        return root
    per_line = 50
    n_lines = max(1, captures // (per_line + 2))
    spec = FlightSpec(
        sensor="rededge_mx",
        n_lines=n_lines,
        captures_per_line=per_line,
        turn_captures=2,
        ground_captures=2,
        seed=42,
    )
    t0 = time.perf_counter()
    print(f"합성 데이터 생성 중: {root} (약 {n_lines * (per_line + 2)} 캡처)")
    res = build_dataset(root, spec, find_exiftool())
    marker.write_text(str(res.n_captures))
    dt = time.perf_counter() - t0
    print(f"  생성 완료: {res.n_captures} 캡처, {len(res.files)} 파일, {dt:.0f} s")
    return root


def run_once(root: Path, workers: int, use_cache: bool) -> BenchResult:
    cfg = load_config()
    cfg.extract.workers = workers
    t0 = time.perf_counter()
    report = pipeline.run(root, cfg, use_cache=use_cache)
    total = time.perf_counter() - t0
    n_files = len(report.discovery.image_files)
    extract = report.timings_s.get("extract", 0.0)
    return BenchResult(
        captures=report.n_captures,
        files=n_files,
        workers=cfg.extract.resolve_workers(),
        cache="hit" if use_cache and report.timings_s.get("cache_hits") else "off",
        discover_s=report.timings_s.get("discover", 0.0),
        extract_s=extract,
        analyze_s=report.timings_s.get("analyze", 0.0),
        total_s=round(total, 2),
        files_per_s=round(n_files / extract, 0) if extract else 0.0,
        status=report.status.value,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--captures", type=int, default=3000)
    ap.add_argument("--real", type=Path, default=None, help="실제 데이터 폴더")
    ap.add_argument("--workers", type=int, nargs="*", default=[0], help="0 = 자동(코어 수)")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args()
    root = ns.real if ns.real else synth_root(ns.captures)
    info = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "root": str(root) if ns.real is None else "<real folder>",
    }
    results = [run_once(root, w, use_cache=False) for w in ns.workers]
    # 캐시 적중 재실행 (증분 캐시 효과)
    run_once(root, ns.workers[0], use_cache=True)  # 채우기
    results.append(run_once(root, ns.workers[0], use_cache=True))
    if ns.json:
        print(
            json.dumps({"info": info, "results": [asdict(r) for r in results]}, ensure_ascii=False)
        )
    else:
        print(json.dumps(info, ensure_ascii=False))
        head = ("workers", "cache", "files", "discover", "extract", "analyze", "total", "files/s")
        print(" ".join(f"{h:>8}" for h in head))
        for r in results:
            row = (
                f"{r.workers:>8} {r.cache:>8} {r.files:>8} {r.discover_s:>8.1f} "
                f"{r.extract_s:>8.1f} {r.analyze_s:>8.2f} {r.total_s:>8.1f} "
                f"{r.files_per_s:>8.0f}"
            )
            print(row)
    target_ok = all(r.extract_s <= 60 for r in results if r.cache == "off")
    print("목표(추출 ≤ 60 s):", "충족" if target_ok else "미충족")
    return 0 if target_ok else 1


if __name__ == "__main__":
    sys.exit(main())
