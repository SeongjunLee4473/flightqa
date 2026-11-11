"""공용 위젯: 배지, 카드, 배너, 드롭존, 섹션 제목.

QSS 의 ``role``/``status`` 속성으로 스타일을 받는다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from flightqa.core.models import Status
from flightqa.gui.theme import current_palette
from flightqa.gui.theme.icons import icon, pixmap

STATUS_KEY = {Status.PASS: "pass", Status.WARN: "warn", Status.FAIL: "fail", Status.NA: "na"}
STATUS_TEXT = {
    Status.PASS: "통과",
    Status.WARN: "주의",
    Status.FAIL: "실패",
    Status.NA: "해당 없음",
}


def repolish(w: QWidget) -> None:
    """동적 속성 변경 후 QSS 재적용."""
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def home_dir() -> str:
    """파일·폴더 대화상자의 기본 위치 = 사용자 홈 폴더 (모든 불러오기·내보내기 대화상자가 쓴다)."""
    return str(Path.home())


def label(text: str = "", role: str | None = None, *, wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


class StatusBadge(QLabel):
    def __init__(self, status: Status = Status.NA, text: str | None = None, size: str = "") -> None:
        super().__init__()
        self.setProperty("role", "badge")
        if size:
            self.setProperty("size", size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_status(status, text)

    def set_status(self, status: Status, text: str | None = None) -> None:
        self.setProperty("status", STATUS_KEY[status])
        self.setText(f"●  {text or STATUS_TEXT[status]}")
        self.setAccessibleName(f"판정 {STATUS_TEXT[status]}")
        repolish(self)


class Card(QFrame):
    """카드 표면. ``clicked`` 를 쓰려면 ``clickable=True``."""

    clicked = Signal()

    def __init__(self, clickable: bool = False, role: str = "card") -> None:
        super().__init__()
        self.setProperty("role", role)
        self.setProperty("clickable", "true" if clickable else "false")
        self._clickable = clickable
        if clickable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(6)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._clickable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: object) -> None:
        if self._clickable and getattr(e, "key", lambda: None)() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.clicked.emit()
            return
        super().keyPressEvent(e)  # type: ignore[arg-type]


class StatCard(Card):
    """요약 수치 카드: 캡션 · 큰 값 · 보조 설명.

    보조 설명 줄은 비어 있어도 자리를 유지해 카드 높이가 나란히 같다.
    """

    def __init__(self, caption: str, value: str = "—", sub: str = "") -> None:
        super().__init__()
        self.caption = label(caption, "caption")
        self.value = label(value, "value")
        self.sub = label("", "muted")
        self.body.addWidget(self.caption)
        self.body.addWidget(self.value)
        self.body.addWidget(self.sub)
        self.set(value, sub)
        self.setMinimumWidth(140)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value: str, sub: str = "") -> None:
        self.value.setText(value)
        self.sub.setText((sub if len(sub) <= 40 else sub[:39] + "…") or " ")
        self.sub.setToolTip(sub)


class Banner(QFrame):
    """인라인 오류·경고 배너 (모달 대신). 자세히 보기 토글 포함."""

    def __init__(self) -> None:
        super().__init__()
        self.setProperty("role", "banner")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        self.icon_lb = QLabel()
        self.text_lb = label("", wrap=True)
        self.text_lb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.detail_btn = QPushButton("자세히")
        self.detail_btn.setProperty("variant", "ghost")
        self.detail_btn.setCheckable(True)
        self.copy_btn = QPushButton("문제 보고 복사")
        self.copy_btn.setProperty("variant", "ghost")
        self.close_btn = QPushButton()
        self.close_btn.setProperty("variant", "ghost")
        self.close_btn.setAccessibleName("배너 닫기")
        top.addWidget(self.icon_lb)
        top.addWidget(self.text_lb, 1)
        top.addWidget(self.detail_btn)
        top.addWidget(self.copy_btn)
        top.addWidget(self.close_btn)
        lay.addLayout(top)
        self.detail = label("", "mono", wrap=True)
        self.detail.setVisible(False)
        lay.addWidget(self.detail)
        self.detail_btn.toggled.connect(self.detail.setVisible)
        self.close_btn.clicked.connect(self.hide)
        self.copy_btn.clicked.connect(self._copy)
        self.hide()

    def show_message(self, level: str, text: str, detail: str = "") -> None:
        self.setProperty("level", level)
        p = current_palette()
        color = {"warn": p.warn, "fail": p.fail, "pass": p.pass_}.get(level, p.text2)
        name = {"warn": "alert", "fail": "x", "pass": "check"}.get(level, "info")
        self.icon_lb.setPixmap(icon(name, color, 18).pixmap(18, 18))
        self.close_btn.setIcon(icon("x", p.text3, 14))
        self.text_lb.setText(text)
        self.detail.setText(detail)
        self.detail_btn.setVisible(bool(detail))
        self.copy_btn.setVisible(bool(detail))
        self.detail_btn.setChecked(False)
        repolish(self)
        self.show()

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(f"{self.text_lb.text()}\n\n{self.detail.text()}")


class DropZone(QFrame):
    """폴더 드롭존. 클릭하면 폴더 선택. 여러 폴더를 한 번에 받을 수 있다."""

    folders_dropped = Signal(list)  # list[Path]
    clicked = Signal()

    def __init__(self, title: str, hint: str) -> None:
        super().__init__()
        self.setProperty("role", "dropzone")
        self.setProperty("active", "false")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        self.setMinimumHeight(180)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(8)
        self.icon_lb = QLabel()
        self.icon_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lb = label(title, "h2")
        self.title_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_lb = label(hint, "muted")
        self.hint_lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.icon_lb)
        lay.addWidget(self.title_lb)
        lay.addWidget(self.hint_lb)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self.icon_lb.setPixmap(icon("folder-open", current_palette().brand, 40).pixmap(40, 40))

    @staticmethod
    def _dirs(mime: QMimeData) -> list[Path]:
        out: list[Path] = []
        for url in mime.urls():
            if url.isLocalFile():
                p = Path(url.toLocalFile())
                out.append(p if p.is_dir() else p.parent)
        return list(dict.fromkeys(out))

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and self._dirs(e.mimeData()):
            e.acceptProposedAction()
            self.setProperty("active", "true")
            repolish(self)

    def dragLeaveEvent(self, _e: QDragLeaveEvent) -> None:
        self.setProperty("active", "false")
        repolish(self)

    def dropEvent(self, e: QDropEvent) -> None:
        self.setProperty("active", "false")
        repolish(self)
        dirs = self._dirs(e.mimeData())
        if dirs:
            e.acceptProposedAction()
            self.folders_dropped.emit(dirs)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: object) -> None:
        if getattr(e, "key", lambda: None)() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            self.clicked.emit()
            return
        super().keyPressEvent(e)  # type: ignore[arg-type]


def section(title: str, *, action: QWidget | None = None, sub: str = "") -> QWidget:
    """섹션 소제목: 브랜드색 세로 막대 + 굵은 제목 (+ 옅은 보조 설명) + 아래 헤어라인.

    모든 화면이 같은 모양을 쓴다 (보고서 HTML 의 h3 와 같은 문법).
    """
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 10, 0, 2)
    v.setSpacing(6)
    row = QHBoxLayout()
    row.setSpacing(10)
    bar = QFrame()
    bar.setProperty("role", "accent")
    row.addWidget(bar)
    row.addWidget(label(title, "section"))
    if sub:
        row.addWidget(label(sub, "section-sub"))
    row.addStretch(1)
    if action is not None:
        row.addWidget(action)
    v.addLayout(row)
    line = QFrame()
    line.setProperty("role", "separator")
    v.addWidget(line)
    return w


def button(
    text: str,
    *,
    variant: str | None = None,
    icon_name: str | None = None,
    on_click: Callable[[], None] | None = None,
) -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        p = current_palette()
        color = {"primary": p.on_brand, "danger": p.fail}.get(variant or "", p.text2)
        b.setIcon(icon(icon_name, color, 16))
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


class HourglassSpinner(QWidget):
    """진행 중임을 알리는 모래시계: 잠시 멈춰 있다가 180° 뒤집기를 반복한다.

    한 주기 = 정지 ``hold_ms`` + 회전 ``flip_ms`` (ease-in-out). 색은 그릴 때마다 브랜드 토큰에서
    읽어 테마 전환을 따라간다. ``stop()`` 은 각도를 0 으로 돌리고 타이머를 멈춘다.
    """

    HOLD_MS = 700
    FLIP_MS = 550
    FRAME_MS = 33
    """30 fps 면 550 ms 뒤집기가 충분히 부드럽고, 분석 중 메인 스레드 부하를 줄인다."""

    def __init__(self, size: int = 26, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._angle = 0.0
        self._elapsed_ms = 0
        self._turns = 0
        self._curve = QEasingCurve(QEasingCurve.Type.InOutCubic)
        self._timer = QTimer(self)
        self._timer.setInterval(self.FRAME_MS)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(QSize(size + 6, size + 6))
        self.setAccessibleName("분석 진행 중")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @property
    def angle(self) -> float:
        return self._angle

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        self._elapsed_ms = 0
        self._turns = 0
        self._angle = 0.0
        self.show()
        self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self._angle = 0.0
        self.update()

    def _tick(self) -> None:
        self._elapsed_ms += self.FRAME_MS
        period = self.HOLD_MS + self.FLIP_MS
        if self._elapsed_ms >= period:
            self._elapsed_ms -= period
            self._turns += 1
        t = self._elapsed_ms - self.HOLD_MS
        frac = 0.0 if t <= 0 else min(t / self.FLIP_MS, 1.0)
        angle = (self._turns + self._curve.valueForProgress(frac)) * 180.0 % 360.0
        if angle != self._angle:  # 정지 구간에는 다시 그리지 않는다 (취소 응답 등 메인 스레드 여유)
            self._angle = angle
            self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        pm = pixmap("hourglass", current_palette().brand, self._size)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.rotate(self._angle)
        w = pm.width() / pm.devicePixelRatio()
        h = pm.height() / pm.devicePixelRatio()
        p.drawPixmap(int(-w / 2), int(-h / 2), pm)
        p.end()


class FileDropZone(DropZone):
    """파일 드롭존: 지정한 확장자의 파일과 폴더(안을 뒤질 용도)를 받는다."""

    files_dropped = Signal(list)  # list[Path] (파일 또는 폴더)

    def __init__(
        self, title: str, hint: str, *, suffixes: Iterable[str], icon_name: str = "file"
    ) -> None:
        self.suffixes = {s.lower() for s in suffixes}
        self._icon_name = icon_name
        super().__init__(title, hint)

    def refresh_icon(self) -> None:
        self.icon_lb.setPixmap(icon(self._icon_name, current_palette().brand, 40).pixmap(40, 40))

    def _paths(self, mime: QMimeData) -> list[Path]:
        out: list[Path] = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir() or p.suffix.lower() in self.suffixes:
                out.append(p)
        return list(dict.fromkeys(out))

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls() and self._paths(e.mimeData()):
            e.acceptProposedAction()
            self.setProperty("active", "true")
            repolish(self)

    def dropEvent(self, e: QDropEvent) -> None:
        self.setProperty("active", "false")
        repolish(self)
        paths = self._paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.files_dropped.emit(paths)


class ConfirmDialog(QDialog):
    """디자인 시스템으로 그린 확인 대화상자 (QMessageBox 대체).

    QMessageBox 는 OS 다크 모드에서 검은 배경에 회색 글자로 떠서 앱과 따로 놀았다. 배경·글자·버튼을
    토큰으로 직접 그리고, 아이콘도 앱 아이콘 세트를 쓴다. ``ask`` 가 True 면 확인을 눌렀다.
    """

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        detail: str = "",
        *,
        ok_text: str = "확인",
        cancel_text: str | None = "취소",
        danger: bool = False,
        icon_name: str = "info",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        p = current_palette()
        # 앱 QSS 와 별개로 자기 배경을 확실히 칠한다 (OS 다크 모드의 검은 대화상자 방지)
        self.setStyleSheet(f"QDialog#root {{ background: {p.bg}; }}")
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 22, 24, 18)
        v.setSpacing(14)
        row = QHBoxLayout()
        row.setSpacing(14)
        ic = QLabel()
        ic.setPixmap(icon(icon_name, p.fail if danger else p.brand, 28).pixmap(28, 28))
        ic.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        self.text_lb = label(text, "h3", wrap=True)
        col.addWidget(self.text_lb)
        self.detail_lb = label(detail, "muted", wrap=True)
        self.detail_lb.setVisible(bool(detail))
        col.addWidget(self.detail_lb)
        row.addLayout(col, 1)
        v.addLayout(row)
        btns = QHBoxLayout()
        btns.addStretch(1)
        if cancel_text:
            self.cancel_btn = button(cancel_text, variant="ghost", on_click=self.reject)
            btns.addWidget(self.cancel_btn)
        self.ok_btn = button(
            ok_text, variant="danger" if danger else "primary", on_click=self.accept
        )
        self.ok_btn.setDefault(True)
        self.ok_btn.setAutoDefault(True)
        btns.addWidget(self.ok_btn)
        v.addLayout(btns)
        self.ok_btn.setFocus()

    @classmethod
    def ask(
        cls,
        parent: QWidget | None,
        title: str,
        text: str,
        detail: str = "",
        *,
        ok_text: str = "확인",
        cancel_text: str | None = "취소",
        danger: bool = False,
        icon_name: str = "info",
    ) -> bool:
        dlg = cls(
            parent,
            title,
            text,
            detail,
            ok_text=ok_text,
            cancel_text=cancel_text,
            danger=danger,
            icon_name=icon_name,
        )
        return dlg.exec() == QDialog.DialogCode.Accepted
