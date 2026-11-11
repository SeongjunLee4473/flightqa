"""앱 아이콘 생성: 로고 SVG 를 브랜드 색으로 렌더해 assets/flightqa.ico (Windows) 와 png 를 만든다.

Qt 의 이미지 플러그인으로 ICO 를 쓰므로 별도 의존성이 없다. Qt 가 없는 환경에서는 건너뛴다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    from flightqa.gui.theme.icons import svg
    from flightqa.theme.tokens import LIGHT

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    ASSETS.mkdir(exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128, 256)
    images: list[QImage] = []
    for size in sizes:
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 둥근 사각 배경(브랜드) + 흰 로고
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LIGHT.brand))
        r = size * 0.22
        p.drawRoundedRect(0, 0, size, size, r, r)
        inner = int(size * 0.72)
        off = (size - inner) // 2
        renderer = QSvgRenderer(QByteArray(svg("logo", "#FFFFFF", inner, 2.2).encode("utf-8")))
        renderer.render(
            p, __import__("PySide6.QtCore", fromlist=["QRectF"]).QRectF(off, off, inner, inner)
        )
        p.end()
        images.append(img)
    images[-1].save(str(ASSETS / "flightqa.png"))
    ok = images[4].save(
        str(ASSETS / "flightqa.ico")
    )  # 64px 단일 프레임 ICO (Qt 는 다중 프레임 미지원)
    print("icon:", ASSETS / "flightqa.ico", "ok" if ok else "실패 (ICO 플러그인 없음)")
    del app
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
