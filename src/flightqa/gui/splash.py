"""시작 화면: 무거운 모듈을 불러오는 동안 보이는 작은 카드.

exe 는 Qt 위젯이 올라온 뒤에도 분석·보고서 모듈을 불러오는 데 수 초가 걸린다. 그 사이 아무것도
보이지 않으면 실행이 안 된 것처럼 보이므로, ``QApplication`` 직후에 이 창을 띄우고 메인 창이 뜨면
닫는다 (``app.main``). 디자인 시스템 토큰을 그대로 쓰고 라이트·다크 테마를 따른다.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from flightqa import __version__
from flightqa.gui.theme import current_palette
from flightqa.gui.theme.icons import pixmap

WIDTH, HEIGHT = 420, 180


class Splash(QWidget):
    """테두리 없는 시작 카드. ``set_status`` 로 단계 문구를 바꾸고 ``finish`` 로 닫는다."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(WIDTH, HEIGHT)
        p = current_palette()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("splashCard")
        card.setStyleSheet(
            f"#splashCard {{ background: {p.bg2}; border: 1px solid {p.border}; "
            "border-radius: 12px; }"
        )
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 24, 28, 22)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(pixmap("logo", p.brand, 44))
        logo.setFixedSize(44, 44)
        head.addWidget(logo)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("FlightQA")
        title.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {p.text};")
        sub = QLabel("UAV 다중분광 비행 데이터 품질 진단")
        sub.setStyleSheet(f"font-size: 12px; color: {p.text3};")
        title_box.addWidget(title)
        title_box.addWidget(sub)
        head.addLayout(title_box)
        head.addStretch(1)
        ver = QLabel(f"v{__version__}")
        ver.setStyleSheet(f"font-size: 12px; color: {p.text3};")
        ver.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        head.addWidget(ver)
        v.addLayout(head)
        v.addStretch(1)

        self.status = QLabel("시작하는 중…")
        self.status.setStyleSheet(f"font-size: 13px; color: {p.text2};")
        self.status.setAccessibleName("시작 진행 상태")
        v.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 진행 중 표시 (양 끝 왕복)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.bar.setStyleSheet(
            f"QProgressBar {{ background: {p.bg3}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {p.brand}; border-radius: 2px; }}"
        )
        v.addWidget(self.bar)
        self._center()

    def _center(self) -> None:
        g = QGuiApplication.primaryScreen().availableGeometry()
        self.move(g.center().x() - WIDTH // 2, g.center().y() - HEIGHT // 2)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        QApplication.processEvents()

    def finish(self, main_window: QWidget) -> None:
        """메인 창이 보이면 닫는다 (메인 창을 활성화해 포커스를 넘긴다)."""
        QApplication.processEvents()
        main_window.raise_()
        main_window.activateWindow()
        self.close()
        self.deleteLater()
