"""``flightqa`` 명령행 인터페이스 (typer + rich).

GUI 와 같은 ``pipeline.run`` 을 호출한다 (CLAUDE.md §6.5).

종료 코드: 0 PASS, 1 WARN, 2 FAIL, 3 판정 불가(N/A), ≥10 오류.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from flightqa import __version__
from flightqa.config.schema import Config, load_config
from flightqa.core import pipeline
from flightqa.core.discovery import OUTPUT_DIR_NAME
from flightqa.core.exiftool import ExifToolNotFoundError, exiftool_version, find_exiftool, read_tags
from flightqa.core.metadata import CancelledError, CancelToken
from flightqa.core.models import RunReport, Status
from flightqa.report import export
from flightqa.report import html as html_report


def _utf8_console() -> None:
    """Windows 콘솔(cp949)·얼린 exe 에서 JSON/한글 출력이 UnicodeEncodeError 로 죽지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


_utf8_console()

app = typer.Typer(
    name="flightqa",
    help="UAV 다중분광 비행 데이터 품질 진단 도구",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)

EXIT_BY_STATUS = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2, Status.NA: 3}
EXIT_ERROR = 10
EXIT_CANCELLED = 11
STATUS_STYLE = {
    Status.PASS: "bold green",
    Status.WARN: "bold yellow",
    Status.FAIL: "bold red",
    Status.NA: "dim",
}


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load(config_path: Path | None) -> Config:
    try:
        return load_config(config_path)
    except Exception as e:
        err_console.print(f"[red]설정 오류:[/] {e}")
        raise typer.Exit(EXIT_ERROR) from e


@app.command()
def version() -> None:
    """버전과 exiftool 상태를 출력한다."""
    console.print(f"flightqa {__version__}")
    try:
        exe = find_exiftool()
        console.print(f"exiftool {exiftool_version(exe)} ({exe})")
    except ExifToolNotFoundError as e:
        err_console.print(f"[yellow]exiftool 없음:[/] {e}")


@app.command()
def tags(
    files: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, help="이미지 파일")],
    numeric: Annotated[bool, typer.Option("--numeric", "-n", help="수치값 그대로 (-n)")] = False,
) -> None:
    """원본 EXIF/XMP 태그를 JSON 으로 덤프한다 (exiftool -j -G1 -struct -a)."""
    try:
        records = read_tags(files, numeric=numeric)
    except ExifToolNotFoundError as e:
        err_console.print(f"[red]오류:[/] {e}")
        raise typer.Exit(EXIT_ERROR) from e
    print(json.dumps(records, indent=2, ensure_ascii=False))


@app.command()
def inspect(
    folder: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="원본 데이터 폴더")],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON 으로 출력")] = False,
) -> None:
    """폴더를 스캔해 센서·캡처 수·그룹만 보여준다 (분석 없음)."""
    cfg = _load(config)
    res = pipeline.inspect(folder, cfg)
    if as_json:
        data = res.model_dump(mode="json")
        data["image_files"] = len(res.image_files)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    t = Table(title=f"발견 결과: {res.root}", show_header=False)
    t.add_row(
        "센서", f"{res.sensor.value if res.sensor else '감지 실패'} (확신도 {res.confidence:.0%})"
    )
    t.add_row("이미지 파일", f"{len(res.image_files):,} (스캔 {res.n_files_scanned:,})")
    t.add_row("잠정 캡처 수", f"{res.n_provisional_captures:,}")
    t.add_row("규칙 불일치 이미지", f"{len(res.unresolved):,}")
    t.add_row("제외 (손상 등)", f"{len(res.ignored):,}")
    console.print(t)
    if res.groups:
        g = Table(title="그룹별 파일 수")
        g.add_column("그룹")
        g.add_column("파일", justify="right")
        for k, v in list(res.groups.items())[:40]:
            g.add_row(k, f"{v:,}")
        console.print(g)


@app.command()
def run(
    folder: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="원본 데이터 폴더")],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help=f"출력 폴더 (기본: <folder>/{OUTPUT_DIR_NAME})"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="요약을 JSON 으로 stdout 에")] = False,
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="메타데이터 캐시 사용 안 함")
    ] = False,
    workers: Annotated[
        int, typer.Option("--workers", "-w", help="exiftool 프로세스 수 (0=자동)")
    ] = 0,
    fast: Annotated[
        bool, typer.Option("--fast", help="초고속 모드: 포화 분석을 4×4 스트라이드 샘플링으로")
    ] = False,
    no_pixels: Annotated[
        bool, typer.Option("--no-pixels", help="픽셀 분석(포화·패널 힌트) 생략 — 메타데이터만")
    ] = False,
    pdf: Annotated[bool, typer.Option("--pdf", help="PDF 보고서도 생성 (QtWebEngine)")] = False,
    lang: Annotated[str, typer.Option("--lang", help="보고서 언어: ko | en")] = "ko",
    no_html: Annotated[bool, typer.Option("--no-html", help="HTML 보고서 생략")] = False,
    mission: Annotated[
        Path | None,
        typer.Option(
            "--mission",
            "-m",
            exists=True,
            dir_okay=False,
            help="조정기에서 내보낸 임무 KMZ/KML. 없으면 폴더 안에서 찾고, 그것도 없으면 궤적 추정",
        ),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """폴더를 분석하고 판정·데이터 파일을 쓴다. 종료 코드 0 PASS / 1 WARN / 2 FAIL."""
    _setup_logging(verbose)
    cfg = _load(config)
    if workers:
        cfg.extract.workers = workers
        cfg.exposure.workers = workers
    if mission is not None:
        cfg.survey_area.mission_path = str(mission)
    if fast:
        cfg.exposure.stride = 4
    out_dir = out or (folder / OUTPUT_DIR_NAME)
    cancel = CancelToken()
    stage_tasks: dict[str, TaskID] = {}
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TextColumn("{task.fields[msg]}"),
        console=console,
        disable=as_json,
        transient=False,
    ) as prog:

        def on_progress(p: pipeline.Progress) -> None:
            label = pipeline.STAGE_LABELS.get(p.stage, p.stage)
            if p.stage not in stage_tasks:
                stage_tasks[p.stage] = prog.add_task(label, total=p.total or None, msg="")
            tid = stage_tasks[p.stage]
            prog.update(tid, completed=p.current, total=p.total or None, msg=_short(p.message))

        try:
            report = pipeline.run(
                folder,
                cfg,
                on_progress=on_progress,
                cancel=cancel,
                use_cache=not no_cache,
                skip_pixels=no_pixels,
            )
        except CancelledError:
            err_console.print("[yellow]취소됨[/]")
            raise typer.Exit(EXIT_CANCELLED) from None
        except ExifToolNotFoundError as e:
            err_console.print(f"[red]오류:[/] {e}")
            raise typer.Exit(EXIT_ERROR) from e
    written = export.write_all(report, out_dir)
    if not no_html:
        html_path = html_report.write_html(report, out_dir / "report.html", lang=lang)
        written.append(html_path)
        if pdf:
            from flightqa.report.pdf import html_to_pdf

            pdf_path = out_dir / "report.pdf"
            if html_to_pdf(html_path, pdf_path):
                written.append(pdf_path)
            else:
                err_console.print("[yellow]PDF 생성 실패:[/] HTML 보고서는 만들어졌습니다.")
    if as_json:
        data = report.model_dump(mode="json")
        for f in data["flights"]:
            f["rows"] = []
        data["discovery"]["image_files"] = []
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_report(report, written)
    raise typer.Exit(EXIT_BY_STATUS[report.status])


def _short(msg: str, n: int = 40) -> str:
    return msg if len(msg) <= n else "…" + msg[-(n - 1) :]


def _print_report(r: RunReport, written: list[Path]) -> None:
    style = STATUS_STYLE[r.status]
    console.rule(
        f"[{style}]{r.status.value}[/] {r.headline}"
        + (f" — {r.headline_detail}" if r.headline_detail else "")
    )
    console.print(
        f"센서 {r.sensor_display or '—'} · 캡처 {r.n_captures:,} · 비행 {len(r.flights)} · "
        f"제외 {len(r.excluded) + len(r.discovery.ignored)} · "
        f"소요 {r.timings_s.get('total', 0):.1f}s (추출 {r.timings_s.get('extract', 0):.1f}s)"
    )
    for w in r.warnings:
        console.print(f"[yellow]주의:[/] {w}")
    for fl in r.flights:
        s = fl.summary
        t = Table(
            title=(
                f"[{STATUS_STYLE[fl.status]}]{fl.status.value}[/] {s.flight_id} — "
                f"캡처 {s.n_captures} · 라인 {s.n_lines} · AGL {s.mean_agl_m or '—'} m · "
                f"간격 {s.median_interval_s or '—'} s · 속도 {s.median_speed_mps or '—'} m/s"
            ),
            expand=True,
        )
        t.add_column("판정", width=6)
        t.add_column("항목", min_width=22)
        t.add_column("측정값", justify="right", min_width=10)
        t.add_column("기준", min_width=18)
        t.add_column("근거")
        for g in fl.groups:
            for m in g.metrics:
                val = "—" if m.value is None else f"{m.value} {m.unit or ''}".strip()
                t.add_row(
                    f"[{STATUS_STYLE[m.status]}]{m.status.value}[/]",
                    m.label,
                    val,
                    m.threshold or "",
                    m.reason,
                )
        console.print(t)
    for g in r.run_groups:
        t = Table(title=f"[{STATUS_STYLE[g.status]}]{g.status.value}[/] {g.label}", expand=True)
        t.add_column("판정", width=6)
        t.add_column("항목", min_width=22)
        t.add_column("측정값", justify="right", min_width=10)
        t.add_column("근거")
        for m in g.metrics:
            val = "—" if m.value is None else f"{m.value} {m.unit or ''}".strip()
            t.add_row(f"[{STATUS_STYLE[m.status]}]{m.status.value}[/]", m.label, val, m.reason)
        console.print(t)
    if r.next_actions:
        console.print("[bold]권고 조치[/]")
        for a in r.next_actions:
            console.print(f"  • {a}")
    console.print("[dim]출력:[/] " + ", ".join(str(p) for p in written))


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    app()


@app.command()
def metashape(
    pdfs: Annotated[
        list[Path], typer.Argument(exists=True, dir_okay=False, help="Metashape 처리 보고서 PDF")
    ],
    flight: Annotated[
        Path | None,
        typer.Option(
            "--flight", "-f", exists=True, help="대조할 FlightQA 결과 (summary.json 또는 결과 폴더)"
        ),
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", exists=True, dir_okay=False)
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="JSON·CSV 저장 폴더")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="판정을 JSON 으로 stdout 에")] = False,
) -> None:
    """Metashape 처리 보고서(PDF)를 읽어 후처리 품질을 판정한다.

    종료 코드 0 PASS / 1 WARN / 2 FAIL / 3 N/A, 읽기 실패 10.
    """
    from flightqa.core.metashape import MetashapeError, judge, load_report
    from flightqa.gui.workers import load_report as load_flight

    cfg = _load(config)
    flight_report = None
    if flight is not None:
        fp = flight / "summary.json" if flight.is_dir() else flight
        flight_report = load_flight(fp)
    worst_code = 0
    results = []
    for pdf in pdfs:
        try:
            rep = load_report(pdf)
        except MetashapeError as e:
            err_console.print(f"[red]{pdf.name}: {e}[/]")
            worst_code = max(worst_code, 10)
            continue
        res = judge(rep, cfg.metashape, flight_report)
        results.append(res)
        if out is not None:
            export.write_metashape(res, out, stem=pdf.stem)
        if not as_json:
            console.print(f"[bold]{pdf.name}[/] · {res.headline} · {res.headline_detail}")
            table = Table(show_header=True, header_style="bold")
            for col in ("판정", "항목", "측정값", "기준", "근거"):
                table.add_column(col)
            for g in res.groups:
                for m in g.metrics:
                    val = f"{m.value} {m.unit or ''}".strip() if m.value is not None else "—"
                    table.add_row(
                        f"[{STATUS_STYLE[m.status]}]{m.status.value}[/]",
                        m.label,
                        val,
                        _short(m.threshold or ""),
                        _short(m.reason),
                    )
            console.print(table)
        worst_code = max(worst_code, EXIT_BY_STATUS[res.status])
    if as_json:
        print(
            json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=2)
        )
    raise typer.Exit(code=worst_code)
