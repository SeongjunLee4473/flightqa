"""분석 화면 (CLAUDE.md §6.3-2).

단계별 진행률, 현재 파일, 남은 시간, 실시간 로그(필터), 취소, 발견 카드.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from flightqa.config.schema import Config
from flightqa.core.models import DiscoveryResult, RunReport
from flightqa.core.sensors import get_sensor
from flightqa.gui.widgets.common import (
    Banner,
    Card,
    HourglassSpinner,
    StatCard,
    button,
    label,
    repolish,
    section,
)
from flightqa.gui.workers import AnalysisWorker

STAGES = [
    ("discover", "발견"),
    ("extract", "메타데이터 추출"),
    ("analyze", "궤적·무결성 분석"),
    ("pixels", "포화·픽셀 분석"),
    ("judge", "판정"),
    ("export", "내보내기"),
    ("render", "보고서"),
]


class StageRow(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        self.title = label(title)
        self.title.setMinimumWidth(130)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setAccessibleName(f"{title} 진행률")
        self.info = label("", "muted")
        self.info.setMinimumWidth(160)
        h.addWidget(self.title)
        h.addWidget(self.bar, 1)
        h.addWidget(self.info)

    def set(self, current: int, total: int, msg: str = "") -> None:
        msg = _elide(msg, 48)
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(current / total * 100))
            self.info.setText(f"{current:,}/{total:,}  {msg}".strip())
        else:  # 총량 미상 → 바쁨 표시
            self.bar.setRange(0, 0)
            self.info.setText(f"{current:,}  {msg}".strip() if current else msg)

    def done(self, ok: bool = True) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(100)
        self.bar.setProperty("status", "done" if ok else "error")
        repolish(self.bar)

    def reset(self) -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setProperty("status", "")
        repolish(self.bar)
        self.info.setText("")


class AnalysisPage(QWidget):
    finished = Signal(object, object)  # RunReport, Path
    cancelled = Signal()
    failed = Signal(str, str)
    open_results = Signal()
    """완료 후 '결과 보기' 버튼."""

    def __init__(self) -> None:
        super().__init__()
        self.worker: AnalysisWorker | None = None
        self._t0 = 0.0
        self._last_stage = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(12)

        head = QHBoxLayout()
        self.spinner = HourglassSpinner(26)
        self.spinner.hide()
        self.title = label("분석 중", "title")
        self.folder_lb = label("", "muted")
        head.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(6)
        head.addWidget(self.title)
        head.addStretch(1)
        self.cancel_btn = button(
            "취소 (Esc)", variant="danger", icon_name="stop", on_click=self.cancel
        )
        self.results_btn = button(
            "결과 보기", variant="primary", icon_name="chart", on_click=self.open_results.emit
        )
        self.results_btn.hide()
        head.addWidget(self.results_btn)
        head.addWidget(self.cancel_btn)
        root.addLayout(head)
        root.addWidget(self.folder_lb)
        self.banner = Banner()
        root.addWidget(self.banner)

        # 발견 카드 (발견 단계가 끝나면 즉시 채움)
        root.addWidget(section("발견"))
        cards = QHBoxLayout()
        self.c_sensor = StatCard("센서", "—")
        self.c_files = StatCard("이미지 파일", "—")
        self.c_caps = StatCard("잠정 캡처", "—")
        self.c_groups = StatCard("그룹(SET/미션)", "—")
        for c in (self.c_sensor, self.c_files, self.c_caps, self.c_groups):
            cards.addWidget(c)
        root.addLayout(cards)

        # 단계 진행률
        root.addWidget(section("진행"))
        panel = Card(role="panel")
        self.rows: dict[str, StageRow] = {}
        for key, title in STAGES:
            r = StageRow(title)
            self.rows[key] = r
            panel.body.addWidget(r)
        foot = QHBoxLayout()
        self.elapsed_lb = label("경과 0:00", "muted")
        self.eta_lb = label("", "muted")
        foot.addWidget(self.elapsed_lb)
        foot.addStretch(1)
        foot.addWidget(self.eta_lb)
        panel.body.addLayout(foot)
        root.addWidget(panel)

        # 로그
        tools = QWidget()
        th = QHBoxLayout(tools)
        th.setContentsMargins(0, 0, 0, 0)
        self.level = QComboBox()
        self.level.addItems(["전체", "WARNING 이상", "ERROR"])
        self.level.setAccessibleName("로그 수준 필터")
        self.search = QLineEdit()
        self.search.setPlaceholderText("로그 검색")
        self.search.setClearButtonEnabled(True)
        th.addWidget(self.level)
        th.addWidget(self.search)
        root.addWidget(section("로그", action=tools))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("role", "log")
        self.log.setMaximumBlockCount(5000)
        self.log.setAccessibleName("분석 로그")
        root.addWidget(self.log, 1)
        self._entries: list[tuple[str, str]] = []
        self.level.currentIndexChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._grid = QGridLayout()

    # ---- 실행
    def start(
        self,
        folder: Path,
        config: Config,
        out_dir: Path,
        *,
        lang: str,
        use_cache: bool,
        skip_pixels: bool = False,
    ) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.title.setText("분석 중")
        self.spinner.start()
        self.results_btn.hide()
        self.cancel_btn.show()
        self.folder_lb.setText(str(folder))
        self.banner.hide()
        for r in self.rows.values():
            r.reset()
        for c in (self.c_sensor, self.c_files, self.c_caps, self.c_groups):
            c.set("—")
        self.log.clear()
        self._entries.clear()
        self._t0 = time.perf_counter()
        self._last_stage = ""
        self.cancel_btn.setEnabled(True)
        w = AnalysisWorker(
            folder,
            config,
            out_dir,
            lang=lang,
            use_cache=use_cache,
            skip_pixels=skip_pixels,
            parent=self,
        )
        w.progress.connect(self._on_progress)
        w.discovered.connect(self._on_discovered)
        w.logged.connect(self._on_log)
        w.finished_ok.connect(self._on_finished)
        w.failed.connect(self._on_failed)
        w.cancelled.connect(self._on_cancelled)
        self.worker = w
        self._timer.start()
        w.start()

    def cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.title.setText("취소 중…")

    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    # ---- 시그널 처리
    def _on_progress(self, stage: str, current: int, total: int, msg: str) -> None:
        if stage != self._last_stage and self._last_stage in self.rows:
            self.rows[self._last_stage].done()
        self._last_stage = stage
        if stage in self.rows:
            self.rows[stage].set(current, total, msg)
        if stage in ("extract", "pixels") and total and current:
            rate = current / max(time.perf_counter() - self._t0, 0.001)
            remain = (total - current) / rate if rate > 0 else 0
            self.eta_lb.setText(f"예상 남은 시간 ~{_fmt(remain)}")

    def _on_discovered(self, d: DiscoveryResult) -> None:
        name = get_sensor(d.sensor).display_name if d.sensor else "감지 실패"
        self.c_sensor.set(name, f"확신도 {d.confidence:.0%}")
        self.c_files.set(f"{len(d.image_files):,}", f"제외 {len(d.ignored)}")
        self.c_caps.set(f"{d.n_provisional_captures:,}", f"규칙 불일치 {len(d.unresolved)}")
        self.c_groups.set(str(len(d.groups)), next(iter(d.groups), ""))
        self.rows["discover"].done()

    def _on_log(self, level: str, text: str) -> None:
        self._entries.append((level, text))
        if self._passes(level, text):
            self.log.appendPlainText(f"[{level[:4]}] {text}")

    def _passes(self, level: str, text: str) -> bool:
        idx = self.level.currentIndex()
        order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        if idx == 1 and order.get(level, 1) < 2:
            return False
        if idx == 2 and order.get(level, 1) < 3:
            return False
        q = self.search.text().strip().lower()
        return not q or q in text.lower()

    def _refilter(self) -> None:
        self.log.clear()
        for level, text in self._entries:
            if self._passes(level, text):
                self.log.appendPlainText(f"[{level[:4]}] {text}")

    def _on_finished(self, report: RunReport, out_dir: Path) -> None:
        self._timer.stop()
        for r in self.rows.values():
            r.done()
        self._stop_spinner()
        self.cancel_btn.hide()
        self.results_btn.show()
        self.title.setText("완료")
        self.eta_lb.setText("")
        self.finished.emit(report, out_dir)

    def _on_failed(self, msg: str, tb: str) -> None:
        self._timer.stop()
        if self._last_stage in self.rows:
            self.rows[self._last_stage].done(ok=False)
        self._stop_spinner()
        self.cancel_btn.hide()
        self.title.setText("실패")
        self.banner.show_message("fail", f"분석 실패: {msg}", tb)
        self.failed.emit(msg, tb)

    def _on_cancelled(self) -> None:
        self._timer.stop()
        self._stop_spinner()
        self.cancel_btn.hide()
        self.title.setText("취소됨")
        self.banner.show_message("warn", "분석을 취소했습니다.")
        self.cancelled.emit()

    def _tick(self) -> None:
        self.elapsed_lb.setText(f"경과 {_fmt(time.perf_counter() - self._t0)}")

    def _stop_spinner(self) -> None:
        self.spinner.stop()
        self.spinner.hide()

    def keyPressEvent(self, e: object) -> None:
        if getattr(e, "key", lambda: None)() == Qt.Key.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(e)  # type: ignore[arg-type]


def _elide(text: str, n: int) -> str:
    return text if len(text) <= n else "…" + text[-(n - 1) :]


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"
