"""GUI 테마: 토큰 → QSS, 아이콘, 테마 적용."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from flightqa.gui.theme.qss import ThemeMode, build_qss, palette_for
from flightqa.theme.tokens import Palette

__all__ = ["ThemeMode", "apply_theme", "build_qss", "current_palette", "palette_for"]

_CURRENT: dict[str, str] = {"mode": "light"}


def apply_theme(app: QApplication, mode: ThemeMode) -> None:
    """앱 전체에 QSS 를 적용한다. 'system' 은 OS 색 구성표를 따른다."""
    if mode == "system":
        mode = _system_mode(app)
    _CURRENT["mode"] = mode
    app.setStyleSheet(build_qss(mode))


def current_palette() -> Palette:
    return palette_for(_CURRENT["mode"])


def current_mode() -> ThemeMode:
    return _CURRENT["mode"]


def _system_mode(app: QApplication) -> ThemeMode:
    try:
        from PySide6.QtCore import Qt

        scheme = app.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"
    except Exception:  # noqa: BLE001 - 구형 Qt 는 colorScheme 이 없다
        return "light"
