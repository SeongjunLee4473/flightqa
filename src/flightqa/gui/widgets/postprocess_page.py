"""후처리 품질 화면: Metashape 처리 보고서(PDF)를 불러와 판정하고, 비행 분석 결과와 대조한다.

분석과 독립된 도구다. PDF 를 드롭존에 놓으면 ``core.metashape`` 가 읽어 표에 요약이 나오고, 행을
고르면 아래에 판정(그룹별 항목 표, 비행 분석 결과 화면과 같은 모양)과 보고서 그림이 나온다.
"비행 분석 대조" 에서 최근 분석을 고르면 교차 검증 그룹이 추가된다. 내보내기는 보고서마다
JSON + CSV (``report.export.write_metashape``).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flightqa.config.schema import Config
from flightqa.core.metashape import (
    MetashapeError,
    MetashapeResult,
    extract_figures,
    judge,
    load_report,
)
from flightqa.core.models import RunReport, Status
from flightqa.gui.state import AppState
from flightqa.gui.widgets.common import (
    Banner,
    FileDropZone,
    StatusBadge,
    button,
    home_dir,
    label,
    section,
)
from flightqa.gui.widgets.results_page import GroupBlock, _badge_cell
from flightqa.report import export

COLUMNS = [
    "파일",
    "보고서 날짜",
    "카메라",
    "영상 (정렬)",
    "GSD (cm/px)",
    "재투영 RMS (px)",
    "위치 오차 XY (m)",
    "판정",
]
FIGURE_H = 200


class _Loaded:
    def __init__(
        self, path: Path, result: MetashapeResult, figures: list[tuple[str, bytes]]
    ) -> None:
        self.path = path
        self.result = result
        self.figures = figures


class PostprocessPage(QWidget):
    results_changed = Signal(int)

    def __init__(self, state: AppState, config_fn: Callable[[], Config]) -> None:
        super().__init__()
        self.state = state
        self._config_fn = config_fn
        self.items: list[_Loaded] = []
        self._paths: list[Path] = []
        self._flight: RunReport | None = None
        self._flight_path: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.page_scroll)
        content = QWidget()
        self.page_scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(12)
        root.addWidget(label("후처리 품질", "title"))
        root.addWidget(
            label(
                "Metashape 의 Generate Report 로 만든 처리 보고서(PDF)를 읽어 정렬·재투영 오차·"
                "보정·위치 정확도·산출물을 판정합니다. 같은 현장의 비행 분석 결과를 고르면 "
                "영상 수·고도·GSD·면적을 대조합니다.",
                "muted",
                wrap=True,
            )
        )
        self.drop = FileDropZone(
            "Metashape 보고서(PDF)를 끌어다 놓거나 클릭해 선택",
            "Metashape → File → Export → Generate Report 로 만든 PDF · "
            "여러 개를 한 번에 놓을 수 있음",
            suffixes={".pdf"},
            icon_name="file",
        )
        self.drop.files_dropped.connect(self.load_paths)
        self.drop.clicked.connect(self.choose_files)
        root.addWidget(self.drop)

        row = QHBoxLayout()
        row.addWidget(
            button("파일 선택…", variant="primary", icon_name="file", on_click=self.choose_files)
        )
        row.addSpacing(12)
        row.addWidget(label("비행 분석 대조", "muted"))
        self.flight_combo = QComboBox()
        self.flight_combo.setMinimumWidth(300)
        self.flight_combo.setAccessibleName("대조할 비행 분석")
        self.flight_combo.currentIndexChanged.connect(self._flight_changed)
        row.addWidget(self.flight_combo)
        row.addWidget(button("summary.json…", on_click=self.choose_flight_file))
        row.addStretch(1)
        self.export_btn = button(
            "내보내기…", variant="primary", icon_name="download", on_click=self.export
        )
        self.export_btn.setEnabled(False)
        row.addWidget(self.export_btn)
        self.clear_btn = button("비우기", variant="danger", icon_name="trash", on_click=self.clear)
        row.addWidget(self.clear_btn)
        root.addLayout(row)
        self.banner = Banner()
        root.addWidget(self.banner)

        root.addWidget(section("불러온 보고서", sub="행을 선택하면 아래에 판정과 그림"))
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("불러온 보고서 표")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # 카메라 열이 남는 폭을 받는다
        hh.setSectionResizeMode(
            7, QHeaderView.ResizeMode.Fixed
        )  # 배지 셀 위젯은 내용 폭에 안 잡힌다
        hh.resizeSection(7, 96)
        hh.setStretchLastSection(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._fit_table()
        root.addWidget(self.table)

        # 판정 헤더
        self.verdict = QFrame()
        self.verdict.setProperty("role", "card")
        vh = QVBoxLayout(self.verdict)
        vh.setContentsMargins(20, 16, 20, 16)
        vh.setSpacing(6)
        top = QHBoxLayout()
        self.badge = StatusBadge(Status.NA)
        self.headline = label("보고서를 불러오면 판정이 여기에 나옵니다", "headline")
        top.addWidget(self.badge)
        top.addSpacing(8)
        top.addWidget(self.headline)
        top.addStretch(1)
        vh.addLayout(top)
        self.detail_lb = label("", "headline-detail", wrap=True)
        vh.addWidget(self.detail_lb)
        self.meta_lb = label("", "muted", wrap=True)
        vh.addWidget(self.meta_lb)
        root.addWidget(self.verdict)

        root.addWidget(section("항목별 판정"))
        self.groups_box = QVBoxLayout()
        self.groups_box.setSpacing(12)
        root.addLayout(self.groups_box)

        root.addWidget(
            section("보고서 그림", sub="카메라 위치·중복도, 위치 오차, DEM (PDF 에서 추출)")
        )
        self.fig_row = QHBoxLayout()
        self.fig_row.setSpacing(12)
        fig_host = QWidget()
        fig_host.setLayout(self.fig_row)
        root.addWidget(fig_host)
        root.addStretch(1)
        self.refresh_flights()

    # ---- 비행 분석 목록
    def refresh_flights(self) -> None:
        self.flight_combo.blockSignals(True)
        cur = self.flight_combo.currentData()
        self.flight_combo.clear()
        self.flight_combo.addItem("대조 안 함", "")
        for e in self.state.recent():
            p = Path(e.out_dir) / "summary.json"
            if p.exists():
                self.flight_combo.addItem(
                    f"{Path(e.path).name} · {e.sensor} · {e.n_captures:,}캡처", str(p)
                )
        if self._flight_path is not None and self.flight_combo.findData(str(self._flight_path)) < 0:
            self.flight_combo.addItem(
                f"{self._flight_path.parent.name} (파일)", str(self._flight_path)
            )
        idx = self.flight_combo.findData(cur) if cur else 0
        self.flight_combo.setCurrentIndex(max(idx, 0))
        self.flight_combo.blockSignals(False)

    def choose_flight_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self,
            "비행 분석 결과 (summary.json)",
            home_dir(),
            "summary.json (summary.json);;JSON (*.json)",
        )
        if f:
            self._set_flight_path(Path(f))
            self.refresh_flights()
            self.flight_combo.setCurrentIndex(self.flight_combo.findData(str(Path(f))))

    def _flight_changed(self, _i: int) -> None:
        data = self.flight_combo.currentData()
        self._set_flight_path(Path(data) if data else None)
        self._rejudge()

    def _set_flight_path(self, p: Path | None) -> None:
        self._flight_path = p
        self._flight = None
        if p is None:
            return
        try:
            from flightqa.gui.workers import load_report as load_flight

            self._flight = load_flight(p)
        except Exception as e:  # noqa: BLE001 - 파일 손상은 배너로 알림
            self.banner.show_message("fail", f"비행 분석 결과를 읽을 수 없습니다: {p}", str(e))
            self._flight = None

    # ---- 불러오기
    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Metashape 보고서 선택", home_dir(), "PDF (*.pdf);;모든 파일 (*)"
        )
        if files:
            self.load_paths([Path(f) for f in files])

    def _cfg(self) -> Config:
        return self._config_fn()

    def load_paths(self, paths: list[Path]) -> None:
        files: list[Path] = []
        for p in paths:
            if p.is_dir():
                files.extend(sorted(p.glob("*.pdf")))
            elif p.suffix.lower() == ".pdf":
                files.append(p)
        if not files:
            self.banner.show_message("warn", "PDF 파일이 없습니다.")
            return
        cfg = self._cfg().metashape
        added, errors = 0, []
        for f in files:
            rp = f.resolve()
            if rp in self._paths:
                continue
            try:
                rep = load_report(rp)
            except MetashapeError as e:
                errors.append(str(e))
                continue
            res = judge(rep, cfg, self._flight)
            self.items.append(_Loaded(rp, res, extract_figures(rp)))
            self._paths.append(rp)
            added += 1
        self._refresh()
        if errors:
            self.banner.show_message(
                "fail" if not added else "warn",
                f"{added}개 불러옴, {len(errors)}개 실패",
                "\n".join(errors),
            )
        elif added:
            self.banner.show_message(
                "pass", f"보고서 {added}개를 불러왔습니다 (총 {len(self.items)}개)."
            )
        else:
            self.banner.show_message("warn", "이미 불러온 파일입니다.")
        self.results_changed.emit(len(self.items))

    def _rejudge(self) -> None:
        cfg = self._cfg().metashape
        for it in self.items:
            it.result = judge(it.result.report, cfg, self._flight)
        self._refresh()

    def clear(self) -> None:
        self.items.clear()
        self._paths.clear()
        self._refresh()
        self.banner.hide()
        self.results_changed.emit(0)

    # ---- 표·상세
    def _refresh(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for it in self.items:
            r = self.table.rowCount()
            self.table.insertRow(r)
            rep = it.result.report
            cams = ", ".join(c.band or c.name for c in rep.cameras) or "—"
            if len(cams) > 40:
                cams = cams[:39] + "…"
            le = rep.location_error
            vals = [
                it.path.name,
                rep.report_date or "—",
                cams,
                f"{rep.n_images or 0:,} ({rep.n_aligned:,})"
                if rep.n_aligned is not None
                else f"{rep.n_images or 0:,} (—)",
                f"{rep.gsd_cm:.2f}" if rep.gsd_cm is not None else "—",
                f"{rep.rms_reprojection_px:.3f}" if rep.rms_reprojection_px is not None else "—",
                f"{le.xy_m:.3f}" if le and le.xy_m is not None else "—",
                "",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (3, 4, 5, 6):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(r, c, item)
            self.table.setCellWidget(r, 7, _badge_cell(it.result.status))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.export_btn.setEnabled(bool(self.items))
        self._fit_table()
        if self.items:
            self.table.selectRow(len(self.items) - 1)
        else:
            self._show(None)

    def _fit_table(self) -> None:
        rows = max(1, min(self.table.rowCount(), 5))
        h = (
            self.table.horizontalHeader().height()
            + rows * self.table.verticalHeader().defaultSectionSize()
            + self.table.horizontalScrollBar().sizeHint().height()
            + 4
        )
        self.table.setFixedHeight(h)

    def _selection_changed(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        self._show(self.items[next(iter(rows))] if rows else None)

    def _show(self, it: _Loaded | None) -> None:
        _clear_layout(self.groups_box)
        _clear_layout(self.fig_row)
        if it is None:
            self.badge.set_status(Status.NA)
            self.headline.setText("보고서를 불러오면 판정이 여기에 나옵니다")
            self.detail_lb.setText("")
            self.meta_lb.setText("")
            return
        res = it.result
        rep = res.report
        self.badge.set_status(res.status)
        self.headline.setText(res.headline)
        self.detail_lb.setText(res.headline_detail)
        meta = (
            f"{it.path.name} · {rep.report_date or '—'} · "
            f"Metashape {rep.software_version or '—'} · "
            f"{rep.n_pages}쪽"
        )
        if res.flight_root:
            meta += f" · 대조: {Path(res.flight_root).name}"
        self.meta_lb.setText(meta)
        for g in res.groups:
            self.groups_box.addWidget(GroupBlock(g))
        for cap, data in it.figures:
            pm = QPixmap()
            if not pm.loadFromData(QByteArray(data)):
                continue
            box = QVBoxLayout()
            box.setSpacing(4)
            img = QLabel()
            img.setPixmap(pm.scaledToHeight(FIGURE_H, Qt.TransformationMode.SmoothTransformation))
            img.setToolTip(cap)
            img.setAccessibleName(cap)
            box.addWidget(img)
            cl = label(cap, "muted", wrap=True)
            cl.setMaximumWidth(int(pm.width() * FIGURE_H / max(pm.height(), 1)))
            box.addWidget(cl)
            host = QWidget()
            host.setLayout(box)
            self.fig_row.addWidget(host)
        self.fig_row.addStretch(1)

    # ---- 내보내기
    def export(self) -> None:
        if not self.items:
            self.banner.show_message("warn", "먼저 보고서를 불러오세요.")
            return
        d = QFileDialog.getExistingDirectory(self, "판정 결과를 저장할 폴더", home_dir())
        if not d:
            return
        written: list[Path] = []
        for it in self.items:
            written.extend(export.write_metashape(it.result, Path(d), stem=it.path.stem))
        self.banner.show_message(
            "pass", f"{len(written)}개 파일을 {d} 에 저장했습니다 (보고서마다 JSON + CSV)."
        )


def _clear_layout(lay: QVBoxLayout | QHBoxLayout) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget() if item is not None else None
        if w is not None:
            w.deleteLater()
