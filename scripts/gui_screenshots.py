"""GUI 스크린샷 생성 (CLAUDE.md §10 M4 완료 기준: 라이트/다크 × 100 %/200 % DPI, 4 화면).

합성 데이터로 실제 분석을 끝까지 돌린 뒤 각 화면을 찍는다. 실제 현장 데이터는 쓰지 않는다.

    uv run python scripts/gui_screenshots.py            # docs/screenshots/gui_*.png
    uv run python scripts/gui_screenshots.py --scale 2  # 200 % DPI

Windows 에서는 네이티브 플랫폼에 화면에 보이지 않는 창(WA_DontShowOnScreen)을 써서 실제 글꼴로
찍는다.
다른 OS 의 offscreen 플랫폼에서는 QtWebEngine 이 종료 시 충돌하므로 보고서 웹뷰를 끈다.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--scale", type=float, default=1.0, help="DPI 배율 (1 = 100 %, 2 = 200 %)")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "screenshots")
    ap.add_argument("--themes", nargs="*", default=["light", "dark"])
    ns = ap.parse_args()

    # Windows: 네이티브 플랫폼 + 화면에 띄우지 않는 창 (실제 글꼴·렌더, WebEngine 종료 충돌 없음).
    # 그 외: offscreen (글꼴이 없어 글자가 네모로 나올 수 있음).
    native = sys.platform == "win32"
    if not native:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["FLIGHTQA_NO_WEBVIEW"] = "1"
    os.environ["QT_SCALE_FACTOR"] = str(ns.scale)
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from conftest import M2_SPEC
    from fixtures.synth import build_dataset
    from flightqa.core.exiftool import find_exiftool
    from flightqa.gui.app import create_app
    from flightqa.gui.main_window import MainWindow
    from flightqa.gui.state import AppState
    from flightqa.gui.theme import apply_theme

    tmp = Path(tempfile.mkdtemp(prefix="flightqa_shots_"))
    data = tmp / "synth_m2"
    build_dataset(data, M2_SPEC, find_exiftool())
    app = create_app()
    assert isinstance(app, QApplication)
    settings = QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat)
    state = AppState(settings)
    state.out_mode = "custom"
    state.out_root = str(tmp / "out")
    win = MainWindow(state)
    if native:
        from PySide6.QtCore import Qt

        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(1280, 820)
    win.show()
    app.processEvents()
    tag = f"{int(ns.scale * 100)}"
    ns.out.mkdir(parents=True, exist_ok=True)

    def shot(name: str) -> None:
        for _ in range(5):
            app.processEvents()
        p = ns.out / f"gui_{name}_{tag}.png"
        win.grab().save(str(p), "PNG")
        print("saved", p.name, win.grab().size().width(), "x", win.grab().size().height())

    # 분석 실행 (결과가 나올 때까지 이벤트 루프)
    done = {"ok": False}
    win.analysis.finished.connect(lambda _r, _o: done.__setitem__("ok", True))
    from test_mission import SQUARE, make_kmz

    win.mission.load_paths(
        [
            make_kmz(
                tmp / "임무-합성A.kmz", SQUARE, [(127.0, 37.0), (127.001, 37.0), (127.001, 37.001)]
            ),
            make_kmz(tmp / "임무-합성B.kmz", [(lo + 0.0012, la) for lo, la in SQUARE]),
        ]
    )
    win.mission.table.selectRow(0)
    win.enqueue([data])
    t0 = time.perf_counter()
    mid_shot = {"taken": False}
    while not done["ok"] and time.perf_counter() - t0 < 300:
        app.processEvents()
        if not mid_shot["taken"] and time.perf_counter() - t0 > 0.8:
            for theme in ns.themes:
                apply_theme(app, theme)
                win.refresh_icons()
                shot(f"analysis_{theme}")
            mid_shot["taken"] = True
        time.sleep(0.02)
    if not done["ok"]:
        print("분석이 끝나지 않음")
        return 1
    from fixtures.metashape_text import make_pdf, ms_report_lines, rgb_report_lines

    win.show_page("postprocess")  # 최근 분석 목록을 채우고 대조 대상으로 고른다
    win.postprocess.flight_combo.setCurrentIndex(1)
    win.postprocess.load_paths(
        [
            make_pdf(tmp / "quality_report_RGB.pdf", rgb_report_lines()),
            make_pdf(tmp / "quality_report_MS.pdf", ms_report_lines()),
        ]
    )
    from flightqa.gui.splash import Splash

    for theme in ns.themes:
        apply_theme(app, theme)
        splash = Splash()
        splash.set_status("화면 구성 요소를 불러오는 중…")
        for _ in range(3):
            app.processEvents()
        splash.grab().save(str(ns.out / f"gui_splash_{theme}_{tag}.png"), "PNG")
        print("saved", f"gui_splash_{theme}_{tag}.png")
        splash.close()
    for theme in ns.themes:
        apply_theme(app, theme)
        win.refresh_icons()
        win.show_page("results")
        shot(f"results_{theme}")
        win.results.tabs.setCurrentIndex(1)
        shot(f"results_detail_{theme}")
        win.results.tabs.setCurrentIndex(0)
        win.show_page("home")
        shot(f"home_{theme}")
        win.show_page("mission")
        shot(f"mission_{theme}")
        win.show_page("postprocess")
        shot(f"postprocess_{theme}")
        win.show_page("settings")
        shot(f"settings_{theme}")
    QTimer.singleShot(0, app.quit)
    app.processEvents()
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
