"""PyInstaller 부트로더 스플래시 이미지(assets/splash.png) 생성.

exe 는 파이썬·Qt 가 올라오기 전(실행 후 1~2 초)에는 아무 창도 띄울 수 없다. PyInstaller 의
부트로더 스플래시(Tcl/Tk)가 그 구간을 덮고, Qt 시작 카드(`gui/splash.py`)가 뜨면 닫힌다
(`gui/app.py` 의 ``pyi_splash.close()``). 두 화면이 같아 보이도록 이 스크립트가 Qt 카드를 그대로
그려 PNG 로 저장한다. 디자인(토큰·로고·문구)이 바뀌면 다시 실행한다:

    uv run python packaging/make_splash.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "splash.png"


def main() -> int:
    # Windows: 네이티브 플랫폼으로 그려야 글꼴이 나온다 (offscreen 은 네모). 창은 띄우지 않는다.
    if sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from flightqa.gui.app import create_app
    from flightqa.gui.splash import Splash
    from flightqa.gui.theme import apply_theme

    app = create_app()
    assert isinstance(app, QApplication)
    apply_theme(app, "light")  # 부트로더 이미지는 정적이라 라이트 테마 하나로
    card = Splash()
    card.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    card.set_status("시작하는 중…")
    for _ in range(3):
        app.processEvents()
    pm = card.grab()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = pm.save(str(OUT), "PNG")
    print(("saved" if ok else "FAILED"), OUT, f"{pm.width()}x{pm.height()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
