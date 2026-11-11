"""GUI 진입점 (``flightqa-gui``). 고 DPI, 테마, 메인 윈도우, 예외 → 배너."""

from __future__ import annotations

import logging
import multiprocessing
import sys

from flightqa.gui.state import APP, ORG, AppState


def create_app() -> object:
    """QApplication 을 만들고 테마를 적용한다 (테스트에서도 사용)."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setOrganizationName(ORG)
    QCoreApplication.setApplicationName(APP)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    from flightqa.gui.theme import apply_theme

    apply_theme(app, AppState().theme)  # type: ignore[arg-type]
    return app


def main() -> int:
    multiprocessing.freeze_support()  # 얼린 exe 에서 픽셀 분석 워커(spawn)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app = create_app()
    # 메인 창 모듈(분석·보고서)을 불러오는 데 수 초가 걸리므로 그동안 시작 카드를 보인다.
    from flightqa.gui.splash import Splash

    splash = Splash()
    splash.show()
    splash.set_status("화면 구성 요소를 불러오는 중…")
    try:  # exe: 부트로더 스플래시(같은 그림)를 Qt 카드가 이어받는다 (packaging/flightqa.spec)
        import pyi_splash  # type: ignore[import-untyped,import-not-found,unused-ignore]

        pyi_splash.close()
    except ImportError:
        pass
    from flightqa.gui.main_window import MainWindow

    splash.set_status("창을 준비하는 중…")
    win = MainWindow()
    win.show()
    splash.finish(win)
    return int(app.exec())  # type: ignore[attr-defined]


if __name__ == "__main__":
    sys.exit(main())
