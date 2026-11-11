"""백그라운드 워커 (CLAUDE.md §6.1: 분석 중 앱이 멈추지 않는다, 취소는 1 초 안에).

``AnalysisWorker`` 는 QThread 에서 ``pipeline.run`` 을 돌리고 진행률·발견 결과·로그를 시그널로
보낸다.
취소는 ``CancelToken`` 을 세우면 파이프라인의 모든 루프가 즉시 확인해 ``CancelledError`` 로
빠져나온다.
"""

from __future__ import annotations

import contextlib
import logging
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from flightqa.config.schema import Config
from flightqa.core.metadata import CancelledError, CancelToken
from flightqa.core.models import DiscoveryResult, RunReport

log = logging.getLogger(__name__)


class _LogBridge(logging.Handler):
    """logging 레코드를 시그널로 (GUI 로그 패널)."""

    def __init__(self, emit_fn: object) -> None:
        super().__init__(level=logging.INFO)
        self._emit_fn = emit_fn

    def emit(self, record: logging.LogRecord) -> None:
        # 로그 전달 실패(위젯이 사라진 뒤 등)는 분석을 막지 않는다.
        with contextlib.suppress(Exception):
            self._emit_fn(record.levelname, self.format(record))  # type: ignore[operator]


class AnalysisWorker(QThread):
    progress = Signal(str, int, int, str)  # stage, current, total, message
    discovered = Signal(object)  # DiscoveryResult
    logged = Signal(str, str)  # level, text
    finished_ok = Signal(object, object)  # RunReport, out_dir(Path)
    failed = Signal(str, str)  # message, traceback
    cancelled = Signal()

    def __init__(
        self,
        folder: Path,
        config: Config,
        out_dir: Path,
        *,
        lang: str = "ko",
        use_cache: bool = True,
        skip_pixels: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.folder = folder
        self.config = config
        self.out_dir = out_dir
        self.lang = lang
        self.use_cache = use_cache
        self.skip_pixels = skip_pixels
        self.cancel_token = CancelToken()
        self.started_at = 0.0

    def cancel(self) -> None:
        self.cancel_token.cancel()

    def run(self) -> None:  # QThread
        self.started_at = time.perf_counter()
        handler = _LogBridge(self.logged.emit)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root = logging.getLogger("flightqa")
        root.addHandler(handler)
        try:
            # 파이프라인·보고서 모듈(pvlib·plotly·jinja2)은 여기서 처음 불러온다 — 시작 시간
            from flightqa.core import pipeline
            from flightqa.report import export
            from flightqa.report import html as html_report

            self.logged.emit("INFO", f"분석 시작: {self.folder}")
            report = pipeline.run(
                self.folder,
                self.config,
                on_progress=lambda p: self.progress.emit(p.stage, p.current, p.total, p.message),
                on_discovery=self.discovered.emit,
                cancel=self.cancel_token,
                use_cache=self.use_cache,
                skip_pixels=self.skip_pixels,
            )
            self.cancel_token.check()
            self.progress.emit("export", 0, 2, "데이터 파일")
            export.write_all(report, self.out_dir)
            self.progress.emit("render", 1, 2, "HTML 보고서")
            html_report.write_html(report, self.out_dir / "report.html", lang=self.lang)
            self.progress.emit("render", 2, 2, "완료")
            self.logged.emit(
                "INFO",
                f"완료: {report.status.value} · {time.perf_counter() - self.started_at:.1f}s",
            )
            self.finished_ok.emit(report, self.out_dir)
        except CancelledError:
            self.logged.emit("WARNING", "사용자 취소")
            self.cancelled.emit()
        except Exception as e:  # noqa: BLE001 - 워커 경계: 어떤 예외든 UI 에 보고
            tb = traceback.format_exc()
            log.error("분석 실패: %s", e)
            self.failed.emit(f"{type(e).__name__}: {e}", tb)
        finally:
            root.removeHandler(handler)


def load_report(summary_json: Path) -> RunReport:
    """이전 분석의 summary.json 을 다시 읽는다 (비교 기능)."""
    return RunReport.model_validate_json(summary_json.read_text(encoding="utf-8"))


__all__ = ["AnalysisWorker", "DiscoveryResult", "load_report"]
