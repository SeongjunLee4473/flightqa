"""단일 세트 선형 아이콘 (24×24, stroke 2). 직접 그린 단순 경로라 라이선스 부담이 없다.

색은 렌더 시 토큰에서 채워 라이트/다크에서 대비를 유지한다.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from flightqa.theme.tokens import LOGO_SVG

_PATHS: dict[str, str] = {
    "logo": LOGO_SVG,
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1H7l-3 8"/><path d="M3 18V7"/>',
    "play": '<path d="M7 5v14l11-7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    "chart": '<path d="M4 20V4"/><path d="M4 20h16"/><path d="M8 16l4-6 3 3 5-7"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "download": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 21h16"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "check": '<path d="M5 12l5 5L20 7"/>',
    "alert": '<path d="M12 3l10 18H2z"/><path d="M12 10v4"/><path d="M12 18h.01"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01"/><path d="M12 11v5"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>',
    "compare": '<path d="M9 3v18"/><path d="M15 3v18"/><path d="M3 8h6"/><path d="M15 8h6"/><path d="M3 16h6"/><path d="M15 16h6"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    "hourglass": '<path d="M6 3h12"/><path d="M6 21h12"/><path d="M7.5 3v3.2L12 12l-4.5 5.8V21"/><path d="M16.5 3v3.2L12 12l4.5 5.8V21"/>',
}


def svg(name: str, color: str, size: int = 24, stroke: float = 2.0) -> str:
    body = _PATHS.get(name, _PATHS["info"])
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 20) -> QIcon:
    """토큰 색으로 칠한 QIcon. 고 DPI 를 위해 2배 픽스맵도 넣는다."""
    result = QIcon()
    for scale in (1, 2):
        px = size * scale
        renderer = QSvgRenderer(QByteArray(svg(name, color, px).encode("utf-8")))
        image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        pm = QPixmap.fromImage(image)
        pm.setDevicePixelRatio(scale)
        result.addPixmap(pm)
    return result


def pixmap(name: str, color: str, size: int = 20) -> QPixmap:
    return icon(name, color, size).pixmap(QSize(size, size))


def names() -> list[str]:
    return sorted(_PATHS)
