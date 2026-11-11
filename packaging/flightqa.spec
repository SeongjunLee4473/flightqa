# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec (CLAUDE.md §9): onedir, GUI exe(FlightQA.exe) + CLI exe(flightqa-cli.exe), exiftool 번들.

    uv run pyinstaller packaging/flightqa.spec --noconfirm

onefile 은 시작이 느리고(매번 압축 해제) 안티바이러스 오탐이 잦아 onedir 로 간다.
두 exe 는 같은 _internal 을 공유한다 (COLLECT 하나).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - PyInstaller 가 주입
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

datas = [
    (str(SRC / "flightqa" / "config" / "default.toml"), "flightqa/config"),
    (str(SRC / "flightqa" / "report" / "templates"), "flightqa/report/templates"),
    (str(SRC / "flightqa" / "report" / "i18n_data"), "flightqa/report/i18n_data"),
]
datas += collect_data_files("plotly", includes=["package_data/plotly.min.js", "package_data/*.json"])
datas += collect_data_files("pvlib", includes=["data/*"])
datas += collect_data_files("pyproj")  # proj.db
datas += collect_data_files("shapely")

binaries = []
exiftool_dir = ROOT / "tools" / "exiftool"
if (exiftool_dir / "exiftool.exe").exists():
    # exiftool.exe + exiftool_files/ (Perl 런타임) 을 tools/exiftool 아래 그대로
    datas.append((str(exiftool_dir), "tools/exiftool"))
else:
    print("경고: tools/exiftool 이 없습니다. scripts/fetch_exiftool.py 를 먼저 실행하세요.")

hiddenimports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
    "PySide6.QtPrintSupport",
    "flightqa.gui.app",
    "flightqa.cli",
    "flightqa.core.analysis.pixels",  # ProcessPool 워커 (spawn) 가 import
    "tifffile",
    "pvlib.solarposition",
    "shapely.geometry",
    "shapely.ops",
    "pyproj",
    "exiftool",
]
hiddenimports += collect_submodules("flightqa")

excludes = [
    "tkinter",
    "matplotlib",
    "IPython",
    "jupyter",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtQuick3D",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtLocation",
    "PySide6.QtPositioning",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
]

block_cipher = None

a_gui = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher_gui.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
a_cli = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher_cli.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
MERGE((a_gui, "FlightQA", "FlightQA"), (a_cli, "flightqa-cli", "flightqa-cli"))  # noqa: F821

pyz_gui = PYZ(a_gui.pure)  # noqa: F821
# 부트로더 스플래시(Tcl/Tk): 파이썬·Qt 가 올라오기 전 1~2 초를 덮는다. 이미지는 packaging/make_splash.py
# 가 Qt 시작 카드를 그대로 그린 것이고, Qt 카드가 뜨면 gui/app.py 가 pyi_splash.close() 로 닫는다.
splash = Splash(  # noqa: F821
    str(ROOT / "assets" / "splash.png"),
    binaries=a_gui.binaries,
    datas=a_gui.datas,
    text_pos=None,
    minify_script=True,
    always_on_top=False,
)
exe_gui = EXE(  # noqa: F821
    pyz_gui,
    a_gui.scripts,
    splash,
    [],
    exclude_binaries=True,
    name="FlightQA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "assets" / "flightqa.ico") if (ROOT / "assets" / "flightqa.ico").exists() else None,
)
pyz_cli = PYZ(a_cli.pure)  # noqa: F821
exe_cli = EXE(  # noqa: F821
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="flightqa-cli",  # Windows 는 대소문자 무시: FlightQA.exe 와 충돌 방지
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(  # noqa: F821
    exe_gui,
    splash.binaries,
    a_gui.binaries,
    a_gui.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    name="FlightQA",
)
