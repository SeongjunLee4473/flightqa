"""메인 윈도우: 사이드바 내비게이션 + 4 화면 스택, 단축키, 드래그앤드롭, 큐 실행, QSettings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from flightqa import __version__
from flightqa.config.schema import Config
from flightqa.core.models import RunReport
from flightqa.gui.state import AppState, RecentEntry
from flightqa.gui.theme import apply_theme, current_mode, current_palette
from flightqa.gui.theme.icons import icon
from flightqa.gui.widgets.analysis_page import AnalysisPage
from flightqa.gui.widgets.common import ConfirmDialog, DropZone, label
from flightqa.gui.widgets.home_page import HomePage
from flightqa.gui.widgets.mission_page import MissionPage
from flightqa.gui.widgets.postprocess_page import PostprocessPage
from flightqa.gui.widgets.results_page import ResultsPage
from flightqa.gui.widgets.settings_page import SettingsPage

log = logging.getLogger(__name__)

PAGES = [
    ("home", "홈", "home"),
    ("analysis", "분석", "play"),
    ("results", "결과", "chart"),
    ("mission", "임무 경로", "layers"),
    ("postprocess", "후처리 품질", "check"),
    ("settings", "설정", "settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None) -> None:
        super().__init__()
        self.state = state or AppState()
        self.setWindowTitle(f"FlightQA {__version__}")
        self.setMinimumSize(960, 640)
        self.setAcceptDrops(True)
        self.queue: list[Path] = []
        self.last_folder: Path | None = None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # 사이드바
        side = QFrame()
        side.setProperty("role", "sidebar")
        side.setFixedWidth(200)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(4)
        brand = QHBoxLayout()
        self.logo = label("")
        self.brand_lb = label("FlightQA", "h2")
        brand.addWidget(self.logo)
        brand.addWidget(self.brand_lb)
        brand.addStretch(1)
        sv.addLayout(brand)
        sv.addSpacing(12)
        self.nav: dict[str, QPushButton] = {}
        for key, text, _ic in PAGES:
            b = QPushButton(text)
            b.setProperty("variant", "nav")
            b.setCheckable(True)
            b.setAccessibleName(f"{text} 화면")
            b.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            sv.addWidget(b)
            self.nav[key] = b
        sv.addStretch(1)
        self.theme_btn = QPushButton("다크 테마")
        self.theme_btn.setProperty("variant", "nav")
        self.theme_btn.clicked.connect(self.toggle_theme)
        sv.addWidget(self.theme_btn)
        sv.addWidget(label(f"v{__version__}", "muted"))
        h.addWidget(side)

        # 페이지
        self.stack = QStackedWidget()
        self.home = HomePage(self.state)
        self.analysis = AnalysisPage()
        self.results = ResultsPage()
        self.settings = SettingsPage(self.state)  # 임무 지도가 배경지도 설정을 읽으므로 먼저 만든다
        self.mission = MissionPage(lambda: self._config().model_dump(mode="json").get("report"))
        self.postprocess = PostprocessPage(self.state, self._config)
        for w in (
            self.home,
            self.analysis,
            self.results,
            self.mission,
            self.postprocess,
            self.settings,
        ):
            self.stack.addWidget(w)  # PAGES 순서와 같아야 한다 (show_page 가 인덱스로 찾음)
        h.addWidget(self.stack, 1)
        self.setStatusBar(self.statusBar())
        self.statusBar().showMessage("폴더를 놓거나 Ctrl+O 로 선택하세요")

        # 연결
        self.home.folders_chosen.connect(self.enqueue)
        self.home.open_result.connect(self._open_recent_result)
        self.analysis.finished.connect(self._on_finished)
        self.analysis.failed.connect(lambda m, _tb: self.statusBar().showMessage(f"실패: {m}"))
        self.analysis.cancelled.connect(self._on_cancelled)
        self.analysis.open_results.connect(lambda: self.show_page("results"))
        self.results.request_rerun.connect(lambda p: self.enqueue([Path(p)]))
        self.settings.theme_changed.connect(self.set_theme)
        self.settings.lang_changed.connect(self._set_lang)

        # 단축키
        self._shortcut("Ctrl+O", self.home.choose_folder)
        self._shortcut("Ctrl+R", self.rerun_last)
        self._shortcut("Ctrl+E", self.results.export_data)
        self._shortcut("Esc", self._escape)
        self._shortcut("Ctrl+1", lambda: self.show_page("home"))
        self._shortcut("Ctrl+2", lambda: self.show_page("analysis"))
        self._shortcut("Ctrl+3", lambda: self.show_page("results"))
        self._shortcut("Ctrl+4", lambda: self.show_page("mission"))
        self._shortcut("Ctrl+5", lambda: self.show_page("postprocess"))
        self._shortcut("Ctrl+,", lambda: self.show_page("settings"))

        g = self.state.geometry()
        if g is not None:
            self.restoreGeometry(g)
        else:
            self.resize(1200, 800)
        self.refresh_icons()
        self.setWindowIcon(icon("logo", current_palette().brand, 64))
        self.show_page("home")

    # ---- 내비게이션
    def show_page(self, key: str) -> None:
        idx = [k for k, _, _ in PAGES].index(key)
        self.stack.setCurrentIndex(idx)
        for k, b in self.nav.items():
            b.setChecked(k == key)
        if key == "home":
            self.home.refresh()
        if key == "settings":
            self.settings.refresh_cache_info()
        if key == "postprocess":
            self.postprocess.refresh_flights()

    def _shortcut(self, seq: str, fn: Callable[[], None]) -> None:
        a = QAction(self)
        a.setShortcut(QKeySequence(seq))
        a.triggered.connect(fn)
        self.addAction(a)

    def _escape(self) -> None:
        if self.analysis.running:
            self.analysis.cancel()

    # ---- 테마
    def toggle_theme(self) -> None:
        self.set_theme("light" if current_mode() == "dark" else "dark")

    def set_theme(self, mode: str) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, mode)
        self.state.theme = mode
        self.refresh_icons()
        self.results.apply_web_theme()
        self.mission.apply_theme()

    def refresh_icons(self) -> None:
        p = current_palette()
        self.logo.setPixmap(icon("logo", p.brand, 22).pixmap(22, 22))
        for key, _t, ic in PAGES:
            self.nav[key].setIcon(icon(ic, p.text2, 16))
        dark = current_mode() == "dark"
        self.theme_btn.setText("라이트 테마" if dark else "다크 테마")
        self.theme_btn.setIcon(icon("sun" if dark else "moon", p.text2, 16))
        self.home.refresh()

    def _set_lang(self, lang: str) -> None:
        self.state.lang = lang

    # ---- 실행 큐
    def enqueue(self, folders: list[Path]) -> None:
        for f in folders:
            if f.is_dir() and f not in self.queue:
                self.queue.append(f)
        self.statusBar().showMessage(f"대기 {len(self.queue)}개")
        self._next()

    def _next(self) -> None:
        if self.analysis.running or not self.queue:
            return
        folder = self.queue.pop(0)
        self.last_folder = folder
        cfg = self._config()
        self.show_page("analysis")
        self.analysis.start(
            folder,
            cfg,
            self.state.out_dir_for(folder),
            lang=self.state.lang,
            use_cache=self.state.use_cache,
        )
        self.statusBar().showMessage(
            f"분석 중: {folder.name}" + (f"  (대기 {len(self.queue)})" if self.queue else "")
        )

    def _config(self) -> Config:
        cfg = self.settings.current_config()
        if self.state.fast_mode:
            cfg.exposure.stride = 4
        if self.state.exiftool_path:
            import os

            os.environ["FLIGHTQA_EXIFTOOL"] = self.state.exiftool_path
        return cfg

    def rerun_last(self) -> None:
        if self.last_folder is not None:
            self.enqueue([self.last_folder])
        elif self.results.report is not None:
            self.enqueue([self.results.report.root])

    def _on_finished(self, report: RunReport, out_dir: Path) -> None:
        self.state.add_recent(
            RecentEntry(
                path=str(report.root),
                sensor=report.sensor_display or "",
                status=report.status.value,
                when=datetime.now(UTC).isoformat(),
                out_dir=str(out_dir),
                n_captures=report.n_captures,
            )
        )
        self.results.show_report(report, out_dir)
        self.show_page("results")
        self.statusBar().showMessage(
            f"{report.status.value} — {report.headline}"
            + (f" · {report.headline_detail}" if report.headline_detail else "")
        )
        if self.queue:
            self._next()

    def _on_cancelled(self) -> None:
        self.queue.clear()
        self.statusBar().showMessage("취소됨")

    def _open_recent_result(self, e: RecentEntry) -> None:
        from flightqa.gui.workers import load_report

        p = Path(e.out_dir) / "summary.json"
        try:
            report = load_report(p)
        except Exception as ex:  # noqa: BLE001 - 파일 손상은 알림
            ConfirmDialog.ask(
                self,
                "결과 열기",
                "결과를 읽을 수 없습니다.",
                str(ex),
                cancel_text=None,
                icon_name="alert",
            )
            return
        self.results.show_report(report, Path(e.out_dir))
        self.show_page("results")

    # ---- 드래그앤드롭 (창 전체)
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        # 임무 파일(KMZ/KML)만 놓으면 임무 경로 화면으로, 폴더면 분석 큐로
        urls = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        files = [p for p in urls if p.suffix.lower() in {".kmz", ".kml"}]
        pdfs = [p for p in urls if p.suffix.lower() == ".pdf"]
        dirs = DropZone._dirs(e.mimeData())
        if files and len(files) == len(urls):
            e.acceptProposedAction()
            self.mission.load_paths(files)
            self.show_page("mission")
            return
        if pdfs and len(pdfs) == len(urls):
            e.acceptProposedAction()
            self.postprocess.load_paths(pdfs)
            self.show_page("postprocess")
            return
        if dirs:
            e.acceptProposedAction()
            self.enqueue(dirs)

    def closeEvent(self, e: QCloseEvent) -> None:
        if self.analysis.running:
            self.analysis.cancel()
            self.analysis.worker.wait(3000) if self.analysis.worker else None
        self.state.set_geometry(self.saveGeometry())
        self.state.sync()
        super().closeEvent(e)

    def keyPressEvent(self, e: object) -> None:
        if getattr(e, "key", lambda: None)() == Qt.Key.Key_Escape:
            self._escape()
        super().keyPressEvent(e)  # type: ignore[arg-type]
