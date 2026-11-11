"""디자인 토큰 → Qt 스타일시트 (CLAUDE.md §6.2). 보고서 CSS 와 같은 팔레트를 쓴다.

기본 Qt 위젯 스타일을 그대로 노출하지 않는다: 모든 위젯에 배경·테두리·반경·hover/pressed/disabled/focus 를 정의한다.
"""

from __future__ import annotations

from flightqa.theme.tokens import (
    DARK,
    FONT_MONO,
    FONT_UI,
    LIGHT,
    RADIUS_LG,
    RADIUS_SM,
    SIZE_LG,
    SIZE_MD,
    SIZE_SM,
    SIZE_XL,
    SIZE_XS,
    Palette,
)

ThemeMode = str  # "light" | "dark"


def palette_for(mode: ThemeMode) -> Palette:
    return DARK if mode == "dark" else LIGHT


def build_qss(mode: ThemeMode) -> str:
    p = palette_for(mode)
    mix = _mix
    return f"""
* {{ font-family: {FONT_UI}; font-size: {SIZE_MD}px; color: {p.text}; }}
QMainWindow, QDialog, QWidget#root {{ background: {p.bg}; }}
QWidget {{ background: transparent; }}
QToolTip {{ background: {p.bg2}; color: {p.text}; border: 1px solid {p.border}; padding: 6px 8px; border-radius: {RADIUS_SM}px; }}

/* ---- 텍스트 */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {SIZE_XL}px; font-weight: 650; }}
QLabel[role="h2"] {{ font-size: {SIZE_LG}px; font-weight: 650; }}
QLabel[role="h3"] {{ font-size: 15px; font-weight: 700; color: {p.text}; }}
QLabel[role="section"] {{ font-size: 16px; font-weight: 700; color: {p.text}; letter-spacing: -0.2px; }}
QLabel[role="section-sub"] {{ font-size: {SIZE_SM}px; color: {p.text3}; }}
QFrame[role="accent"] {{ background: {p.brand}; border: none; border-radius: 2px; min-width: 4px; max-width: 4px; min-height: 16px; max-height: 16px; }}
QLabel[role="headline"] {{ font-size: 20px; font-weight: 700; letter-spacing: -0.3px; }}
QLabel[role="headline-detail"] {{ font-size: {SIZE_MD}px; color: {p.text2}; }}
QLabel[role="muted"] {{ color: {p.text3}; font-size: {SIZE_SM}px; }}
QLabel[role="caption"] {{ color: {p.text3}; font-size: {SIZE_XS}px; text-transform: uppercase; }}
QLabel[role="value"] {{ font-size: {SIZE_LG}px; font-weight: 650; }}
QLabel[role="mono"] {{ font-family: {FONT_MONO}; font-size: {SIZE_SM}px; }}

/* ---- 표면 */
QFrame[role="card"] {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; }}
QFrame[role="panel"] {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_LG}px; }}
QFrame[role="card"]:hover[clickable="true"] {{ border-color: {p.brand}; }}
QFrame[role="sidebar"] {{ background: {p.bg2}; border-right: 1px solid {p.border}; }}
QFrame[role="toolbar"] {{ background: {p.bg2}; border-bottom: 1px solid {p.border}; }}
QFrame[role="separator"] {{ background: {p.border}; max-height: 1px; min-height: 1px; border: none; }}
QFrame[role="dropzone"] {{ background: {p.bg2}; border: 2px dashed {p.border}; border-radius: {RADIUS_LG}px; }}
QFrame[role="dropzone"][active="true"] {{ border-color: {p.brand}; background: {p.brand_soft}; }}
QFrame[role="banner"] {{ border-radius: {RADIUS_SM}px; border: 1px solid {p.border}; background: {p.bg3}; }}
QFrame[role="banner"][level="warn"] {{ background: {p.warn_soft}; border-color: {mix(p.warn)}; }}
QFrame[role="banner"][level="fail"] {{ background: {p.fail_soft}; border-color: {mix(p.fail)}; }}
QFrame[role="banner"][level="pass"] {{ background: {p.pass_soft}; border-color: {mix(p.pass_)}; }}
QFrame[role="verdict"] {{ background: {p.bg2}; border: 2px solid {mix(p.na, 55)}; border-radius: {RADIUS_LG}px; }}
QFrame[role="verdict"][status="pass"] {{ border-color: {mix(p.pass_, 55)}; }}
QFrame[role="verdict"][status="warn"] {{ border-color: {mix(p.warn, 55)}; }}
QFrame[role="verdict"][status="fail"] {{ border-color: {mix(p.fail, 55)}; }}
QFrame[role="pane"] {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; border-top-left-radius: 0; }}
QFrame[role="group-head"] {{ background: {p.bg3}; border: 1px solid {p.border}; border-bottom: none; border-top-left-radius: {RADIUS_SM}px; border-top-right-radius: {RADIUS_SM}px; }}
QTableWidget[role="group-table"] {{ border-top-left-radius: 0; border-top-right-radius: 0; }}

/* ---- 배지 */
QLabel[role="gate"] {{ font-size: 10px; font-weight: 600; color: {p.text3}; border: 1px solid {p.border}; border-radius: 6px; padding: 0px 5px; background: transparent; }}
QLabel[role="badge"] {{ padding: 2px 10px; border-radius: 10px; font-size: {SIZE_SM}px; font-weight: 700; border: 1px solid {p.border}; background: {p.na_soft}; color: {p.na}; }}
QLabel[role="badge"][status="pass"] {{ background: {p.pass_soft}; color: {p.pass_}; border-color: {mix(p.pass_)}; }}
QLabel[role="badge"][status="warn"] {{ background: {p.warn_soft}; color: {p.warn}; border-color: {mix(p.warn)}; }}
QLabel[role="badge"][status="fail"] {{ background: {p.fail_soft}; color: {p.fail}; border-color: {mix(p.fail)}; }}
QLabel[role="badge"][status="na"] {{ background: {p.na_soft}; color: {p.na}; }}
QLabel[role="badge"][size="xl"] {{ font-size: {SIZE_LG}px; padding: 6px 18px; border-radius: 16px; }}

/* ---- 버튼 */
QPushButton {{ background: {p.bg2}; color: {p.text}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; padding: 7px 14px; font-weight: 600; min-height: 18px; }}
QPushButton:hover {{ background: {p.bg3}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.text3}; background: {p.bg3}; border-color: {p.border}; }}
QPushButton:focus {{ outline: none; border: 2px solid {p.focus}; padding: 6px 13px; }}
QPushButton[variant="primary"] {{ background: {p.brand}; color: {p.on_brand}; border-color: {p.brand}; }}
QPushButton[variant="primary"]:hover {{ background: {mix(p.brand, 85)}; }}
QPushButton[variant="primary"]:pressed {{ background: {mix(p.brand, 70)}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.na_soft}; color: {p.text3}; border-color: {p.border}; }}
QPushButton[variant="danger"] {{ color: {p.fail}; border-color: {mix(p.fail)}; }}
QPushButton[variant="danger"]:hover {{ background: {p.fail_soft}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.bg3}; }}
QPushButton[variant="nav"] {{ background: transparent; border: none; border-radius: {RADIUS_SM}px; text-align: left; padding: 9px 12px; font-weight: 600; color: {p.text2}; }}
QPushButton[variant="nav"]:hover {{ background: {p.bg3}; color: {p.text}; }}
QPushButton[variant="nav"]:checked {{ background: {p.brand_soft}; color: {p.brand}; }}
QPushButton[variant="nav"]:focus {{ border: 2px solid {p.focus}; padding: 7px 10px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS_SM}px; padding: 4px; }}
QToolButton:hover {{ background: {p.bg3}; }}
QToolButton:pressed {{ background: {p.border}; }}
QToolButton:focus {{ border-color: {p.focus}; }}

/* ---- 입력 */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; padding: 5px 8px; selection-background-color: {p.brand}; selection-color: {p.on_brand}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {p.focus}; padding: 4px 7px; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {p.text3}; background: {p.bg3}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {p.bg2}; border: 1px solid {p.border}; selection-background-color: {p.brand_soft}; selection-color: {p.text}; outline: none; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {p.border}; border-radius: 4px; background: {p.bg2}; }}
QCheckBox::indicator:checked {{ background: {p.brand}; border-color: {p.brand}; }}
QCheckBox::indicator:focus {{ border-color: {p.focus}; }}

/* ---- 진행률 */
QProgressBar {{ background: {p.bg3}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {p.brand}; border-radius: 4px; }}
QProgressBar[status="done"]::chunk {{ background: {p.pass_}; }}
QProgressBar[status="error"]::chunk {{ background: {p.fail}; }}

/* ---- 목록·표 */
QListWidget, QTableWidget, QTableView, QTreeView {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; outline: none; alternate-background-color: {p.bg}; gridline-color: {p.border}; selection-background-color: {p.brand_soft}; selection-color: {p.text}; }}
QListWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {p.border}; }}
QListWidget::item:hover {{ background: {p.bg3}; }}
QListWidget::item:selected {{ background: {p.brand_soft}; color: {p.text}; }}
QTableWidget::item, QTableView::item {{ padding: 6px 8px; }}
QTableWidget::item:hover, QTableView::item:hover {{ background: {mix(p.brand, 8 if mode == "light" else 14)}; color: {p.text}; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {p.brand_soft}; color: {p.text}; }}
QTableWidget::item:selected:hover, QTableView::item:selected:hover {{ background: {p.brand_soft}; color: {p.text}; }}
QHeaderView::section {{ background: {p.bg3}; color: {p.text3}; padding: 6px 8px; border: none; border-bottom: 1px solid {p.border}; border-right: 1px solid {p.border}; font-size: {SIZE_XS}px; font-weight: 600; text-transform: uppercase; }}
QTableCornerButton::section {{ background: {p.bg3}; border: none; }}

/* ---- 스크롤바 */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {p.text3}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0; height: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---- 탭·분할·기타 */
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; top: -1px; background: {p.bg2}; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; color: {p.text2}; font-weight: 600; }}
QTabBar::tab:selected {{ color: {p.brand}; border-bottom-color: {p.brand}; }}
QTabBar::tab:hover {{ color: {p.text}; }}
QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QStatusBar {{ background: {p.bg2}; border-top: 1px solid {p.border}; color: {p.text3}; font-size: {SIZE_SM}px; }}
QMenu {{ background: {p.bg2}; border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.brand_soft}; color: {p.text}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: {RADIUS_SM}px; margin-top: 14px; padding: 10px 8px 6px; font-weight: 650; color: {p.text2}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
QPlainTextEdit[role="log"] {{ font-family: {FONT_MONO}; font-size: {SIZE_SM}px; }}
"""


def _mix(color: str, pct: int = 40) -> str:
    """색을 배경 쪽으로 옅게 (테두리용). pct = 색 비율 %."""
    c = color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return f"rgba({r},{g},{b},{pct / 100:.2f})"
