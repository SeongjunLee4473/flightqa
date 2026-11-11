"""홈 화면 (CLAUDE.md §6.3-1): 드롭존, 최근 폴더, 프리셋 선택.

최근 폴더 행: 아이콘 · 이름/메타 · 판정 배지 · [다시 분석] [목록에서 제거] 아이콘 버튼.
더블클릭은 지난 결과 열기, 우클릭 메뉴는 다시 분석 / 지난 결과 열기 / 목록에서 제거.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from flightqa.core.models import Status
from flightqa.gui.state import AppState, RecentEntry, presets_dir
from flightqa.gui.theme import current_palette
from flightqa.gui.theme.icons import icon
from flightqa.gui.widgets.common import (
    ConfirmDialog,
    DropZone,
    StatusBadge,
    button,
    home_dir,
    label,
    section,
)

_STATUS = {s.value: s for s in Status}


def _has_result(e: RecentEntry) -> bool:
    return bool(e.out_dir) and (Path(e.out_dir) / "summary.json").exists()


def _icon_button(name: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(name, current_palette().text2, 16))
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tip)
    b.setAccessibleName(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class RecentRow(QWidget):
    rerun = Signal(object)  # RecentEntry
    remove = Signal(object)  # RecentEntry

    def __init__(self, e: RecentEntry) -> None:
        super().__init__()
        self.entry = e
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 6, 6, 6)
        h.setSpacing(10)
        p = current_palette()
        ic = QLabel()
        ic.setPixmap(icon("folder", p.text3, 18).pixmap(18, 18))
        h.addWidget(ic)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = label(Path(e.path).name or e.path)
        name.setStyleSheet("font-weight:600")
        meta = label(
            " · ".join(
                x
                for x in (
                    e.sensor,
                    f"{e.n_captures:,} 캡처" if e.n_captures else "",
                    e.when_dt.astimezone().strftime("%Y-%m-%d %H:%M") if e.when_dt else "",
                )
                if x
            ),
            "muted",
        )
        # 글자 선택 메뉴(복사·전체 선택)가 뜨지 않게 — 행의 우클릭은 목록 메뉴만
        for lb in (name, meta):
            lb.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        col.addWidget(name)
        col.addWidget(meta)
        h.addLayout(col, 1)
        st = _STATUS.get(e.status)
        if st is not None:
            h.addWidget(StatusBadge(st))
        h.addSpacing(16)
        self.rerun_btn = _icon_button("refresh", "다시 분석")
        self.remove_btn = _icon_button("trash", "목록에서 제거")
        self.rerun_btn.clicked.connect(lambda: self.rerun.emit(self.entry))
        self.remove_btn.clicked.connect(lambda: self.remove.emit(self.entry))
        h.addWidget(self.rerun_btn)
        h.addWidget(self.remove_btn)
        self.setToolTip(e.path)


class HomePage(QWidget):
    folders_chosen = Signal(list)  # list[Path]
    open_result = Signal(object)  # RecentEntry

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(14)

        root.addWidget(label("FlightQA", "title"))
        root.addWidget(
            label(
                "원본 데이터 폴더를 놓으면 비행 메타데이터를 분석해 "
                "후처리 투입 가능 여부를 판정합니다.",
                "muted",
                wrap=True,
            )
        )

        self.drop = DropZone(
            "폴더를 끌어다 놓거나 클릭해 선택",
            "RedEdge-MX 0000SET 폴더 또는 M3M 미션 폴더 · 여러 폴더를 한 번에 놓으면 차례로 분석",
        )
        self.drop.folders_dropped.connect(self.folders_chosen.emit)
        self.drop.clicked.connect(self.choose_folder)
        root.addWidget(self.drop)

        row = QHBoxLayout()
        row.addWidget(label("설정 프리셋", "muted"))
        self.preset = QComboBox()
        self.preset.setMinimumWidth(240)
        self.preset.setAccessibleName("설정 프리셋")
        row.addWidget(self.preset)
        row.addStretch(1)
        self.open_btn = button(
            "폴더 선택…", variant="primary", icon_name="folder-open", on_click=self.choose_folder
        )
        self.open_btn.setShortcut("Ctrl+O")
        row.addWidget(self.open_btn)
        root.addLayout(row)

        clear = button("비우기", variant="danger", icon_name="trash", on_click=self._clear_recent)
        root.addWidget(
            section("최근 폴더", action=clear, sub="더블클릭: 지난 결과 열기 · 우클릭: 메뉴")
        )
        self.recent = QListWidget()
        self.recent.setAccessibleName("최근 폴더")
        self.recent.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_menu)
        self.recent.itemActivated.connect(self._recent_activated)
        self.recent.itemDoubleClicked.connect(self._recent_activated)
        root.addWidget(self.recent, 1)
        self.refresh()

    # ---- 프리셋
    def refresh_presets(self) -> None:
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("기본 (default.toml)", "")
        for p in sorted(presets_dir().glob("*.toml")):
            self.preset.addItem(p.stem, str(p))
        cur = self.state.preset_path
        idx = self.preset.findData(cur)
        self.preset.setCurrentIndex(idx if idx >= 0 else 0)
        self.preset.blockSignals(False)
        self.preset.currentIndexChanged.connect(self._preset_changed)

    def _preset_changed(self, _i: int) -> None:
        self.state.preset_path = str(self.preset.currentData() or "")

    def refresh(self) -> None:
        self.refresh_presets()
        self.recent.clear()
        for e in self.state.recent():
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, e)
            row = RecentRow(e)
            row.rerun.connect(self.confirm_rerun)
            row.remove.connect(self.confirm_remove)
            item.setSizeHint(QSize(row.sizeHint().width(), max(row.sizeHint().height(), 64)))
            self.recent.addItem(item)
            self.recent.setItemWidget(item, row)
        self.drop.refresh_icon()

    # ---- 동작
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "원본 데이터 폴더 선택", home_dir())
        if d:
            self.folders_chosen.emit([Path(d)])

    def _recent_activated(self, item: QListWidgetItem) -> None:
        """더블클릭·Enter: 지난 결과 열기. 결과 파일이 없으면 다시 분석할지 묻는다."""
        e = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(e, RecentEntry):
            return
        if _has_result(e):
            self.open_result.emit(e)
        else:
            self.confirm_rerun(e, missing=True)

    def confirm_rerun(self, e: RecentEntry, *, missing: bool = False) -> None:
        if ConfirmDialog.ask(
            self,
            "다시 분석",
            "저장된 결과가 없습니다. 지금 다시 분석할까요?"
            if missing
            else "이 폴더를 다시 분석할까요?",
            f"{e.path}\n기존 결과 파일은 새 결과로 바뀝니다.",
            ok_text="다시 분석",
            icon_name="refresh",
        ):
            self.folders_chosen.emit([Path(e.path)])

    def confirm_remove(self, e: RecentEntry) -> None:
        if ConfirmDialog.ask(
            self,
            "목록에서 제거",
            "이 항목을 최근 목록에서 지울까요?",
            f"{e.path}\n분석 결과 파일은 그대로 남습니다.",
            ok_text="제거",
            danger=True,
            icon_name="trash",
        ):
            self.state.remove_recent(e.path)
            self.refresh()

    def _recent_menu(self, pos: QPoint) -> None:
        item = self.recent.itemAt(pos)
        if item is None:
            return
        e = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(e, RecentEntry):
            return
        menu = QMenu(self)
        a_run = menu.addAction("다시 분석")
        a_open = menu.addAction("지난 결과 열기")
        a_open.setEnabled(_has_result(e))
        a_rm = menu.addAction("목록에서 제거")
        chosen = menu.exec(self.recent.mapToGlobal(pos))
        if chosen is a_run:
            self.confirm_rerun(e)
        elif chosen is a_open:
            self.open_result.emit(e)
        elif chosen is a_rm:
            self.confirm_remove(e)

    def _clear_recent(self) -> None:
        if not self.state.recent():
            return
        if ConfirmDialog.ask(
            self,
            "최근 목록 비우기",
            "최근 폴더 목록을 모두 지울까요?",
            "분석 결과 파일은 그대로 남습니다.",
            ok_text="모두 지우기",
            danger=True,
            icon_name="trash",
        ):
            self.state.clear_recent()
            self.refresh()
