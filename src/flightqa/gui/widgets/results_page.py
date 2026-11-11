"""결과 화면 (CLAUDE.md §6.3-3).

종합 판정 헤더(짧은 결론 + 근거 + 구조화된 권고 조치), 항목 카드(클릭→상세), 항목별 표(그룹마다
표 하나), 보고서 뷰(지도·차트), 내보내기, 비교.
"""

from __future__ import annotations

import html as html_mod
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QPainter,
    QPalette,
    QTextDocument,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from flightqa.core.models import ActionItem, CheckGroup, FlightReport, Metric, RunReport, Status
from flightqa.core.textfmt import split_sentences
from flightqa.gui.theme import current_mode, current_palette
from flightqa.gui.widgets.common import (
    STATUS_TEXT,
    Banner,
    Card,
    StatCard,
    StatusBadge,
    button,
    home_dir,
    label,
    section,
)
from flightqa.gui.workers import load_report

ROW_MIN_H = 44
"""표 행 최소 높이 — 판정 배지(높이 ≈ 26) 가 위아래 여백을 두고 온전히 보인다."""
MAX_ACTIONS_SHOWN = 6


def _fmt(m: Metric) -> str:
    if m.value is None:
        return "—"
    if isinstance(m.value, float):
        return f"{m.value:,.2f} {m.unit or ''}".strip()
    return f"{m.value} {m.unit or ''}".strip()


def _esc(s: str) -> str:
    return html_mod.escape(s, quote=False)


def _status_color(status: Status) -> str:
    p = current_palette()
    return {Status.PASS: p.pass_, Status.WARN: p.warn, Status.FAIL: p.fail}.get(status, p.na)


# --------------------------------------------------------------------------- 표


class RichTextDelegate(QStyledItemDelegate):
    """근거·권고 조치 열: 글머리표가 있는 리치 텍스트(UserRole 의 HTML)를 그리고,
    열 너비에 맞춰 행 높이를 잰다."""

    PAD_X = 10
    PAD_Y = 8

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def _doc(self, index: QModelIndex | QPersistentModelIndex, width: int) -> QTextDocument:
        doc = QTextDocument()
        doc.setDefaultFont(self._table.font())
        doc.setDocumentMargin(0)
        html = index.data(Qt.ItemDataRole.UserRole)
        doc.setHtml(str(html) if html else _esc(str(index.data() or "")))
        doc.setTextWidth(max(40, width - 2 * self.PAD_X))
        return doc

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        doc = self._doc(index, opt.rect.width())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, QColor(current_palette().text))
        h = doc.size().height()
        y = opt.rect.top() + max(self.PAD_Y, int((opt.rect.height() - h) / 2))
        painter.save()
        painter.setClipRect(opt.rect)
        painter.translate(opt.rect.left() + self.PAD_X, y)
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        width = self._table.columnWidth(index.column()) or option.rect.width()
        doc = self._doc(index, width)
        return QSize(width, int(doc.size().height()) + 2 * self.PAD_Y)


def reason_html(m: Metric) -> str:
    """근거 → 권고 조치 → 기준 을 글머리표 3 종으로. 문장은 마침표 뒤에서 줄을 바꾼다."""
    p = current_palette()
    rows: list[tuple[str, str, str]] = []  # (글머리, 색, 본문 HTML)
    if m.reason:
        rows.append(("•", p.text2, "<br>".join(_esc(s) for s in split_sentences(m.reason))))
    if m.next_action:
        rows.append(("→", p.brand, "<br>".join(_esc(s) for s in split_sentences(m.next_action))))
    if m.threshold:
        rows.append(
            ("▸", p.text3, f'<span style="color:{p.text3}">기준: {_esc(m.threshold)}</span>')
        )
    if not rows:
        return ""
    body = "".join(
        f'<tr><td width="14" valign="top"><span style="color:{c}">{g}</span></td>'
        f'<td valign="top">{t}</td></tr>'
        for g, c, t in rows
    )
    return f'<table cellspacing="0" cellpadding="1">{body}</table>'


def _center_item(text: str, tooltip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if tooltip:
        it.setToolTip(tooltip)
    return it


class _LabelCell(QWidget):
    """항목 이름 + (게이트 항목이면) 보고서와 같은 GATE 배지.

    이름이 한 줄에 들어가면 배지를 바로 옆에 붙여 가운데 정렬하고, 안 들어가면 이름만 줄바꿈한다
    (셀 위젯은 표의 자동 줄바꿈을 받지 못하므로 폭이 바뀔 때마다 직접 정한다).
    """

    def __init__(self, text: str, gate: bool) -> None:
        super().__init__()
        self._text = text
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)
        h.addStretch(1)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        h.addWidget(self.label)
        self.gate: QLabel | None = None
        if gate:
            g = QLabel("GATE")
            g.setProperty("role", "gate")
            g.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            g.setToolTip("게이트 항목: 실패하면 전체 판정이 '재비행 검토' 가 된다")
            g.setAccessibleName("게이트 항목")
            h.addWidget(g)
            self.gate = g
        h.addStretch(1)

    def resizeEvent(self, e: object) -> None:
        super().resizeEvent(e)  # type: ignore[arg-type]
        natural = self.label.fontMetrics().horizontalAdvance(self._text) + 4
        badge = (self.gate.sizeHint().width() + 6) if self.gate is not None else 0
        avail = max(40, self.width() - 12 - badge)
        fits = natural <= avail
        self.label.setWordWrap(not fits)
        self.label.setFixedWidth(natural if fits else avail)


def _label_cell(text: str, gate: bool) -> QWidget:
    return _LabelCell(text, gate)


def _badge_cell(status: Status) -> QWidget:
    """셀 한가운데 고정 크기 배지 (행 높이가 달라도 배지 크기는 같다)."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(6, 4, 6, 4)
    b = StatusBadge(status)
    b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    h.addWidget(b, 0, Qt.AlignmentFlag.AlignCenter)
    return w


class AutoHeightTable(QTableWidget):
    """스크롤 없이 내용 높이에 맞춰 커지는 표 (그룹별 표를 세로로 쌓기 위해)."""

    def __init__(self, columns: int) -> None:
        super().__init__(0, columns)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 현재 셀 강조(진한 색) 없이
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setMinimumSectionSize(ROW_MIN_H)
        self.setWordWrap(True)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.horizontalHeader().setHighlightSections(False)
        self._last_w = -1

    def fit(self) -> None:
        self._relayout_columns()
        self.resizeRowsToContents()
        h = self.horizontalHeader().height() + 2 * self.frameWidth()
        for r in range(self.rowCount()):
            h += self.rowHeight(r)
        self.setFixedHeight(h)

    def _relayout_columns(self) -> None:
        """판정·측정값은 고정, 항목은 폭의 28 %(150–300 px), 나머지는 근거 열.

        창이 좁아도 근거 열이 한 어절씩 끊기지 않게 항목 열이 먼저 줄어든다.
        """
        if self.columnCount() != 4:
            return
        w = self.viewport().width()
        c0, c2 = 100, 110
        c1 = max(150, min(300, int(w * 0.28)))
        self.setColumnWidth(0, c0)
        self.setColumnWidth(1, c1)
        self.setColumnWidth(2, c2)
        self.setColumnWidth(3, max(160, w - c0 - c1 - c2))

    def resizeEvent(self, e: object) -> None:
        super().resizeEvent(e)  # type: ignore[arg-type]
        if self.width() != self._last_w:
            self._last_w = self.width()
            QTimer.singleShot(0, self.fit)


class GroupBlock(QWidget):
    """그룹 제목 줄(배지·이름·개수) + 항목 표."""

    COLUMNS = ("판정", "항목", "측정값", "근거 / 권고 조치")

    def __init__(self, group: CheckGroup) -> None:
        super().__init__()
        self.group = group
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QFrame()
        head.setProperty("role", "group-head")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 12, 8)
        hl.setSpacing(10)
        hl.addWidget(StatusBadge(group.status))
        hl.addWidget(label(group.label, "h3"))
        n_fail = sum(1 for m in group.metrics if m.status is Status.FAIL)
        n_warn = sum(1 for m in group.metrics if m.status is Status.WARN)
        counts = f"{len(group.metrics)}개 항목"
        if n_fail or n_warn:
            counts += f" · 실패 {n_fail} · 주의 {n_warn}"
        hl.addWidget(label(counts, "muted"))
        hl.addStretch(1)
        v.addWidget(head)

        self.table = AutoHeightTable(len(self.COLUMNS))
        self.table.setProperty("role", "group-table")
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)  # 폭은 _relayout_columns 가 정한다
        hh.setStretchLastSection(True)
        self.table.setItemDelegateForColumn(3, RichTextDelegate(self.table))
        self.table.setAccessibleName(f"{group.label} 항목 표")
        self.table.setRowCount(len(group.metrics))
        for i, m in enumerate(group.metrics):
            self.table.setCellWidget(i, 0, _badge_cell(m.status))
            self.table.setCellWidget(i, 1, _label_cell(m.label, m.gate))
            self.table.setItem(i, 2, _center_item(_fmt(m)))
            it = QTableWidgetItem("")
            it.setData(Qt.ItemDataRole.UserRole, reason_html(m))
            plain = m.reason + (f"\n→ {m.next_action}" if m.next_action else "")
            it.setToolTip(plain)
            self.table.setItem(i, 3, it)
        v.addWidget(self.table)


class MetricPanel(QWidget):
    """항목별 상세: 그룹마다 표 하나, 표 사이 여백. 스크롤은 결과 화면 전체가 담당한다."""

    scroll_request = Signal(int)  # 화면 안 y 좌표 (부모 스크롤이 처리)

    def __init__(self) -> None:
        super().__init__()
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(10, 0, 0, 0)
        self.vbox.setSpacing(16)
        self.blocks: dict[str, GroupBlock] = {}
        self.setAccessibleName("항목별 판정 표")

    def fill(self, groups: list[CheckGroup]) -> None:
        _clear(self.vbox)
        self.blocks = {}
        for g in groups:
            b = GroupBlock(g)
            self.blocks[g.key] = b
            self.vbox.addWidget(b)
        self.vbox.addStretch(1)
        QTimer.singleShot(0, self._fit_all)

    def _fit_all(self) -> None:
        for b in self.blocks.values():
            b.table.fit()

    def rowCount(self) -> int:
        return sum(b.table.rowCount() for b in self.blocks.values())

    def scroll_to(self, group_key: str) -> None:
        b = self.blocks.get(group_key)
        if b is None:
            return
        QTimer.singleShot(0, lambda: self.scroll_request.emit(b.y()))


class GroupCard(Card):
    """항목 그룹 카드. 항상 3 줄(제목·개수·대표 항목)이라 카드 높이가 같다."""

    def __init__(self, group: CheckGroup) -> None:
        super().__init__(clickable=True)
        self.group = group
        top = QHBoxLayout()
        top.addWidget(label(group.label, "h3"))
        top.addStretch(1)
        top.addWidget(StatusBadge(group.status))
        self.body.addLayout(top)
        n_warn = sum(1 for m in group.metrics if m.status is Status.WARN)
        n_fail = sum(1 for m in group.metrics if m.status is Status.FAIL)
        worst = next(
            (m for m in group.metrics if m.status is Status.FAIL),
            next((m for m in group.metrics if m.status is Status.WARN), None),
        )
        summary = f"{len(group.metrics)}개 항목"
        if n_fail or n_warn:
            summary += f" · 실패 {n_fail} · 주의 {n_warn}"
        self.body.addWidget(label(summary, "muted"))
        if worst is not None:
            line = f"{worst.label}: {_fmt(worst)}"
        elif group.status is Status.PASS:
            line = "모든 항목 기준 충족"
        else:
            line = "판정 대상 없음"
        self.body.addWidget(label(line, "", wrap=True))
        self.setAccessibleName(f"{group.label} {group.status.value}")


# --------------------------------------------------------------------------- 비교


class ComparePanel(QWidget):
    """이전 분석(summary.json)과 항목별 나란히 비교."""

    def __init__(self) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.pick_btn = button(
            "이전 결과 선택… (summary.json)", icon_name="compare", on_click=self._pick
        )
        self.info = label("같은 현장의 이전 분석과 항목별로 비교합니다.", "muted")
        row.addWidget(self.pick_btn)
        row.addWidget(self.info, 1)
        v.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["항목", "이전", "이전 판정", "현재", "현재 판정"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setMinimumSectionSize(ROW_MIN_H)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.table, 1)
        self.current: RunReport | None = None
        self.banner = Banner()
        v.addWidget(self.banner)

    def set_current(self, report: RunReport) -> None:
        self.current = report
        self.table.setRowCount(0)

    def _pick(self) -> None:
        f, _ = QFileDialog.getOpenFileName(
            self, "이전 분석 결과", home_dir(), "summary.json (summary.json);;JSON (*.json)"
        )
        if f:
            self.compare_with(Path(f))

    def compare_with(self, path: Path) -> None:
        if self.current is None:
            return
        try:
            prev = load_report(path)
        except Exception as e:  # noqa: BLE001 - 잘못된 파일은 배너로
            self.banner.show_message("fail", f"결과 파일을 읽을 수 없습니다: {e}")
            return
        self.info.setText(
            f"이전: {prev.root.name} · {prev.generated_at:%Y-%m-%d %H:%M} · {prev.status.value}"
        )
        rows: list[tuple[str, Metric | None, Metric | None]] = []
        cur_m = {m.key: m for f in self.current.flights for g in f.groups for m in g.metrics}
        prev_m = {m.key: m for f in prev.flights for g in f.groups for m in g.metrics}
        for key in sorted(set(cur_m) | set(prev_m)):
            rows.append((key, prev_m.get(key), cur_m.get(key)))
        self.table.setRowCount(len(rows))
        for i, (key, a, b) in enumerate(rows):
            ref = b if b is not None else a
            lab = ref.label if ref is not None else key
            self.table.setItem(i, 0, QTableWidgetItem(lab))
            self.table.setItem(i, 1, _center_item(_fmt(a) if a else "—"))
            self.table.setCellWidget(i, 2, _badge_cell(a.status if a else Status.NA))
            self.table.setItem(i, 3, _center_item(_fmt(b) if b else "—"))
            self.table.setCellWidget(i, 4, _badge_cell(b.status if b else Status.NA))
        self.table.resizeRowsToContents()


class _CurrentSizeStack(QStackedWidget):
    """현재 페이지의 크기 힌트만 보고하는 스택 — 페이지 전체 스크롤에서 탭 높이가 내용에 맞는다."""

    def sizeHint(self) -> QSize:
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


class Tabs(QWidget):
    """QTabBar + 현재 페이지 크기의 스택. QTabWidget 과 같은 최소 API (addTab/setCurrentIndex/…)."""

    currentChanged = Signal(int)  # noqa: N815 - QTabWidget 과 같은 이름

    def __init__(self) -> None:
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.bar = QTabBar()
        self.bar.setExpanding(False)
        self.bar.setDrawBase(False)
        self.stack = _CurrentSizeStack()
        self.pane = QFrame()
        self.pane.setProperty("role", "pane")
        pl = QVBoxLayout(self.pane)
        pl.setContentsMargins(10, 10, 10, 10)
        pl.addWidget(self.stack)
        v.addWidget(self.bar)
        v.addWidget(self.pane)
        self.bar.currentChanged.connect(self._changed)

    def addTab(self, w: QWidget, title: str) -> int:
        self.stack.addWidget(w)
        i = self.bar.addTab(title)
        self._apply_policies(self.bar.currentIndex())
        return i

    def setCurrentIndex(self, i: int) -> None:
        self.bar.setCurrentIndex(i)

    def currentIndex(self) -> int:
        return self.bar.currentIndex()

    def count(self) -> int:
        return self.bar.count()

    def _apply_policies(self, cur: int) -> None:
        """보이지 않는 페이지가 스택의 최소·권장 높이에 끼어들지 않게 Ignored 로 둔다."""
        for k in range(self.stack.count()):
            w = self.stack.widget(k)
            if w is None:
                continue
            w.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Preferred if k == cur else QSizePolicy.Policy.Ignored,
            )
        self.stack.updateGeometry()

    def _changed(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._apply_policies(i)
        self.currentChanged.emit(i)


# --------------------------------------------------------------------------- 결과 화면


class ResultsPage(QWidget):
    request_rerun = Signal(object)  # Path

    def __init__(self) -> None:
        super().__init__()
        self.report: RunReport | None = None
        self.out_dir: Path | None = None
        self._web: QWidget | None = None
        self._web_h = 0
        # 화면 전체가 하나의 스크롤: 판정 카드·버튼·탭 내용이 함께 내려간다
        # (탭 안에 작은 스크롤을 두지 않는다)
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
        root.setContentsMargins(32, 24, 32, 20)
        root.setSpacing(12)

        # 판정 헤더: 배지 + 짧은 결론 / 근거 한 줄 / 메타 / 권고 조치 목록
        self.verdict = Card(role="verdict")
        self.verdict.body.setContentsMargins(20, 16, 20, 16)
        self.verdict.body.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.badge = StatusBadge(Status.NA, size="xl")
        self.headline = label("", "headline", wrap=True)
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.headline, 1)
        self.verdict.body.addLayout(head)
        self.detail_lb = label("", "headline-detail", wrap=True)
        self.verdict.body.addWidget(self.detail_lb)
        self.meta = label("", "muted", wrap=True)
        self.verdict.body.addWidget(self.meta)
        self.actions_section = section("권고 조치")
        self.verdict.body.addWidget(self.actions_section)
        self.actions_lb = label("", "", wrap=True)
        self.actions_lb.setTextFormat(Qt.TextFormat.RichText)
        self.verdict.body.addWidget(self.actions_lb)
        root.addWidget(self.verdict)

        # 버튼 줄
        btns = QHBoxLayout()
        self.open_report_btn = button(
            "보고서 열기", variant="primary", icon_name="external", on_click=self.open_report
        )
        self.pdf_btn = button("PDF 저장", icon_name="download", on_click=self.save_pdf)
        self.export_btn = button("데이터 내보내기", icon_name="file", on_click=self.export_data)
        self.export_btn.setShortcut("Ctrl+E")
        self.folder_btn = button("결과 폴더 열기", icon_name="folder", on_click=self.open_folder)
        self.rerun_btn = button("재분석", icon_name="refresh", on_click=self._rerun)
        self.rerun_btn.setShortcut("Ctrl+R")
        for b in (
            self.open_report_btn,
            self.pdf_btn,
            self.export_btn,
            self.folder_btn,
            self.rerun_btn,
        ):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)
        self.banner = Banner()
        root.addWidget(self.banner)

        # 탭: 요약 / 상세 표 / 보고서 / 비교
        self.tabs = Tabs()
        root.addWidget(self.tabs)
        root.addStretch(1)

        # 요약 탭
        summary = QWidget()
        sv = QVBoxLayout(summary)
        sv.addWidget(section("비행 요약"))
        self.cards_row = QHBoxLayout()
        sv.addLayout(self.cards_row)
        sv.addWidget(section("항목별 판정", sub="카드를 클릭하면 상세로 이동"))
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(10)
        sv.addWidget(self.grid_host)
        self.tabs.addTab(summary, "요약")

        # 상세 탭
        detail = QWidget()
        dv = QVBoxLayout(detail)
        split = QSplitter()
        self.flight_list = QTableWidget(0, 3)
        self.flight_list.setHorizontalHeaderLabels(["비행", "캡처", "판정"])
        self.flight_list.verticalHeader().setVisible(False)
        self.flight_list.verticalHeader().setDefaultSectionSize(ROW_MIN_H)
        self.flight_list.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.flight_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.flight_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.flight_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.flight_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.flight_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.flight_list.setVerticalScrollMode(QTableWidget.ScrollMode.ScrollPerPixel)
        fh = self.flight_list.horizontalHeader()
        fh.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        fh.setHighlightSections(False)
        fh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        fh.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        fh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.flight_list.setColumnWidth(1, 64)
        self.flight_list.setColumnWidth(2, 96)
        self.flight_list.setMinimumWidth(280)
        self.flight_list.setMaximumWidth(360)
        self.flight_list.setAccessibleName("비행 목록")
        self.flight_list.itemSelectionChanged.connect(self._flight_selected)
        self.flight_list.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.table = MetricPanel()
        self.table.scroll_request.connect(self._scroll_to_group)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.flight_list)
        lv.addStretch(1)
        split.addWidget(left)
        split.addWidget(self.table)
        split.setStretchFactor(1, 1)
        dv.addWidget(split)
        self.tabs.addTab(detail, "상세")

        # 보고서 탭 (지도·차트: 생성한 HTML 을 그대로)
        self.report_host = QWidget()
        self.report_layout = QVBoxLayout(self.report_host)
        self.report_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self.report_host, "보고서 (지도·차트)")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._wheel_filter = _WebWheelForward(self)

        # 비교 탭
        self.compare = ComparePanel()
        self.tabs.addTab(self.compare, "이전 분석과 비교")

    # ---- 표시
    def show_report(self, report: RunReport, out_dir: Path) -> None:
        self.report = report
        self.out_dir = out_dir
        self.banner.hide()
        self.badge.set_status(report.status)
        self.headline.setText(report.headline)
        self.detail_lb.setText(report.headline_detail)
        self.detail_lb.setVisible(bool(report.headline_detail))
        n_ex = len(report.excluded) + len(report.discovery.ignored)
        self.meta.setText(
            f"{report.root}  ·  {report.sensor_display or '—'}  ·  캡처 {report.n_captures:,}"
            f"  ·  비행 {len(report.flights)}  ·  제외 {n_ex}"
            f"  ·  {report.timings_s.get('total', 0):.1f}s"
        )
        self.actions_lb.setText(actions_html(report))
        # 카드
        _clear(self.cards_row)
        f0: FlightReport | None = report.flights[0] if report.flights else None
        if f0 is not None:
            s = f0.summary
            ov = {m.key: m for g in f0.groups for m in g.metrics}
            for cap, val, sub in (
                ("캡처", f"{s.n_captures:,}", f"밴드 누락 없음 {s.n_complete:,}건"),
                ("비행 시간", _dur(s.duration_s), "분:초"),
                ("조사 라인", str(s.n_lines), "전환 제외" if s.n_lines else ""),
                (
                    "평균 AGL",
                    f"{s.mean_agl_m:.1f} m" if s.mean_agl_m is not None else "—",
                    f0.agl_source,
                ),
                (
                    "종중복",
                    _fmt(ov["overlap.forward"]) if "overlap.forward" in ov else "—",
                    "실측 중앙값",
                ),
                ("GSD", _fmt(ov["overlap.gsd"]) if "overlap.gsd" in ov else "—", "지상표본거리"),
            ):
                self.cards_row.addWidget(StatCard(cap, val, sub))
        _clear(self.grid)
        groups = (f0.groups if f0 else []) + list(report.run_groups)
        for i, g in enumerate(groups):
            card = GroupCard(g)
            card.clicked.connect(lambda g=g: self._open_group(g))
            self.grid.addWidget(card, i // 3, i % 3)
        # 상세
        self.flight_list.setRowCount(len(report.flights))
        for i, f in enumerate(report.flights):
            self.flight_list.setItem(i, 0, _center_item(f.summary.flight_id, f.summary.flight_id))
            self.flight_list.setItem(i, 1, _center_item(str(f.summary.n_captures)))
            self.flight_list.setCellWidget(i, 2, _badge_cell(f.status))
        self.flight_list.setFixedHeight(
            self.flight_list.horizontalHeader().height()
            + ROW_MIN_H * max(1, len(report.flights))
            + 2 * self.flight_list.frameWidth()
        )
        if report.flights:
            self.flight_list.selectRow(0)
        else:
            self.table.fill(list(report.run_groups))
        # 보고서 뷰
        self._load_report_view(out_dir / "report.html")
        self.compare.set_current(report)
        self.tabs.setCurrentIndex(0)

    def _flight_selected(self) -> None:
        if self.report is None:
            return
        rows = self.flight_list.selectionModel().selectedRows()
        if not rows:
            return
        f = self.report.flights[rows[0].row()]
        self.table.fill(list(f.groups) + list(self.report.run_groups))

    def _open_group(self, g: CheckGroup) -> None:
        self.tabs.setCurrentIndex(1)
        self.table.scroll_to(g.key)

    def _scroll_to_group(self, y_in_panel: int) -> None:
        host = self.page_scroll.widget()
        if host is None:
            return
        pos = self.table.mapTo(host, self.table.rect().topLeft())
        self.page_scroll.verticalScrollBar().setValue(max(0, pos.y() + y_in_panel - 12))

    def _on_tab_changed(self, index: int) -> None:
        if index == 2 and self._web is not None:
            self._fit_web_height()
            page = getattr(self._web, "page", None)
            if callable(page):
                # 웹뷰가 숨겨진 채로 로드되면 차트가 옛 크기로 남을 수 있어, 보일 때 다시 맞춘다
                QTimer.singleShot(
                    50,
                    lambda: page().runJavaScript("window.dispatchEvent(new Event('resize'))"),
                )
            proxy = self._web.focusProxy()
            if proxy is not None and not self._wheel_filter.installed:
                proxy.installEventFilter(self._wheel_filter)
                self._wheel_filter.installed = True
            # 보고서가 창을 가득 채우도록 판정 카드·버튼은 위로 넘긴다
            QTimer.singleShot(0, self._scroll_to_web)

    def _fit_web_height(self) -> None:
        """보고서 웹뷰 높이 = 결과 화면 뷰포트 높이 (탭 패널 여백 제외).

        문서 높이(1 만 px 이상)만큼 키우면 Chromium 이 아래쪽을 그리지 않으므로 창 높이로 두고,
        보고서 안 스크롤과 바깥 스크롤을 휠 방향에 따라 이어 준다 (_WebWheelForward).
        """
        if self._web is None:
            return
        h = self.page_scroll.viewport().height() - 24
        if h > 300 and h != self._web_h:
            self._web_h = h
            self._web.setFixedHeight(h)
            self.tabs.stack.updateGeometry()

    def _scroll_to_web(self) -> None:
        host = self.page_scroll.widget()
        if self._web is None or host is None:
            return
        y = self._web.mapTo(host, self._web.rect().topLeft()).y()
        self.page_scroll.verticalScrollBar().setValue(max(0, y - 12))

    def resizeEvent(self, e: object) -> None:
        super().resizeEvent(e)  # type: ignore[arg-type]
        if self.tabs.currentIndex() == 2:
            self._fit_web_height()

    def apply_web_theme(self) -> None:
        """GUI 테마를 보고서 웹뷰에 전달한다 (페이지의 저장된 테마 대신 GUI 가 주도)."""
        page = getattr(self._web, "page", None)
        if callable(page):
            page().runJavaScript(
                f"window.__flightqa_setTheme&&window.__flightqa_setTheme('{current_mode()}')"
            )

    def _load_report_view(self, html: Path) -> None:
        if self._web is None:
            if os.environ.get("FLIGHTQA_NO_WEBVIEW"):  # 테스트·offscreen: WebEngine 종료 충돌 회피
                self._web = QLabel("보고서 뷰 비활성화 (FLIGHTQA_NO_WEBVIEW)")
                self.report_layout.addWidget(self._web)
                return
            try:
                from PySide6.QtWebEngineCore import QWebEngineSettings
                from PySide6.QtWebEngineWidgets import QWebEngineView

                view = QWebEngineView()
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
                )
                # 보고서의 "위성 배경" (온라인 타일) 이 file:// 페이지에서도 열리도록
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
                )
                self._web = view
            except Exception as e:  # noqa: BLE001 - WebEngine 이 없으면 안내만
                self._web = QLabel(
                    f"보고서 뷰를 열 수 없습니다: {e}\n'보고서 열기' 로 브라우저에서 보세요."
                )
            self.report_layout.addWidget(self._web)
        loader = getattr(self._web, "load", None)
        if html.exists() and callable(loader):
            self._web_h = 0
            self._web.setMinimumHeight(400)
            url = QUrl.fromLocalFile(str(html.resolve()))
            # embedded: 테마 버튼을 숨기고 테마는 GUI 를 따른다
            url.setFragment(f"embedded&theme={current_mode()}")
            loader(url)

    # ---- 버튼
    def open_report(self) -> None:
        if self.out_dir and (self.out_dir / "report.html").exists():
            _open_path(self.out_dir / "report.html")

    def open_folder(self) -> None:
        if self.out_dir:
            _open_path(self.out_dir)

    def save_pdf(self) -> None:
        if not self.out_dir:
            return
        html = self.out_dir / "report.html"
        target, _ = QFileDialog.getSaveFileName(
            self, "PDF 저장", str(self.out_dir / "report.pdf"), "PDF (*.pdf)"
        )
        if not target:
            return
        from flightqa.report.pdf import html_to_pdf

        self.pdf_btn.setEnabled(False)
        try:
            ok = html_to_pdf(html, Path(target))
        finally:
            self.pdf_btn.setEnabled(True)
        if ok:
            self.banner.show_message("pass", f"PDF 저장: {target}")
        else:
            self.banner.show_message(
                "fail", "PDF 생성에 실패했습니다. HTML 보고서를 브라우저에서 인쇄해 보세요."
            )

    def export_data(self) -> None:
        if not self.out_dir:
            return
        d = QFileDialog.getExistingDirectory(self, "데이터 내보내기 폴더", home_dir())
        if not d:
            return
        import shutil

        from flightqa.report.export import EXPORT_FILES

        n = 0
        for name in (*EXPORT_FILES, "report.html"):
            src = self.out_dir / name
            if src.exists():
                shutil.copy2(src, Path(d) / name)
                n += 1
        self.banner.show_message("pass", f"{n}개 파일을 {d} 에 복사했습니다.")

    def _rerun(self) -> None:
        if self.report is not None:
            self.request_rerun.emit(self.report.root)


class _WebWheelForward(QObject):
    """보고서 웹뷰 위의 휠: 아래로 굴릴 때 바깥 페이지가 아직 끝이 아니면 바깥을 먼저 내린다.

    위로 굴려 보고서 맨 위에 닿으면 QtWebEngine 이 처리하지 못한 휠을 부모로 넘기므로
    바깥이 올라간다.
    """

    def __init__(self, page: ResultsPage) -> None:
        super().__init__(page)
        self.page = page
        self.installed = False

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.Wheel and isinstance(ev, QWheelEvent):
            sb = self.page.page_scroll.verticalScrollBar()
            dy = ev.angleDelta().y()
            if dy < 0 and sb.value() < sb.maximum():
                sb.setValue(min(sb.maximum(), sb.value() - dy))
                return True
        return super().eventFilter(obj, ev)


def actions_html(report: RunReport) -> str:
    """권고 조치을 리치 텍스트 목록으로: 상태색 점 · 항목명 · (비행 · 그룹) · 문장별 줄바꿈.

    FAIL 이 먼저 오도록 이미 정렬돼 있다 (rules.judge_run). 6 건까지 보이고 나머지는 상세 탭 안내.
    """
    p = current_palette()
    items: list[ActionItem] = list(report.action_items)
    if not items:
        text = report.next_actions[0] if report.next_actions else "추가 조치 없음"
        return f'<span style="color:{p.text2}">{_esc(text)}</span>'
    multi = len(report.flights) > 1
    rows: list[str] = []
    for it in items[:MAX_ACTIONS_SHOWN]:
        meta: list[str] = []
        if it.flight_index is not None and multi:
            meta.append(f"비행 {it.flight_index + 1}")
        meta.append(it.group_label)
        body = "<br>".join(_esc(s) for s in split_sentences(it.text))
        rows.append(
            f'<tr><td width="14" valign="top"><span style="color:{_status_color(it.status)}">●'
            f'</span></td><td valign="top"><b>{_esc(it.metric_label)}</b>'
            f' <span style="color:{p.text3}">· {_esc(" · ".join(meta))} · '
            f"{STATUS_TEXT[it.status]}</span><br>{body}</td></tr>"
        )
    if len(items) > MAX_ACTIONS_SHOWN:
        rows.append(
            f'<tr><td></td><td><span style="color:{p.text3}">외 '
            f"{len(items) - MAX_ACTIONS_SHOWN}건 — 상세 탭에서 확인</span></td></tr>"
        )
    return '<table cellspacing="0" cellpadding="2">' + "".join(rows) + "</table>"


def _clear(layout: QHBoxLayout | QGridLayout | QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget() if item is not None else None
        if w is not None:
            w.deleteLater()


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _open_path(p: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(p))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except OSError:
        pass
