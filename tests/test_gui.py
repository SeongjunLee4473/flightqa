"""GUI 핵심 흐름 (pytest-qt, offscreen).

실행 → 폴더 → 분석 → 결과, 취소 1 초, 설정·프리셋, 최근 목록, 테마.
결과 화면의 보고서 웹뷰는 테스트 속도를 위해 끈다 (FLIGHTQA_NO_WEBVIEW).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Windows: 네이티브 플랫폼 + WA_DontShowOnScreen
# (offscreen 은 QtWebEngine 종료 시 충돌, docs/ASSUMPTIONS.md M3).
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FLIGHTQA_NO_WEBVIEW"] = "1"

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from fixtures.synth import BuildResult
from flightqa.core.models import Status
from flightqa.gui.main_window import MainWindow
from flightqa.gui.state import AppState, RecentEntry
from flightqa.gui.theme import apply_theme, build_qss, current_mode
from flightqa.gui.theme.icons import icon, names
from flightqa.gui.widgets.common import Banner, HourglassSpinner, StatusBadge

pytestmark = pytest.mark.qt


@pytest.fixture
def state(tmp_path: Path) -> AppState:
    s = AppState(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    s.out_mode = "custom"
    s.out_root = str(tmp_path / "out")
    return s


@pytest.fixture
def window(qtbot: object, state: AppState) -> MainWindow:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app, "light")
    w = MainWindow(state)
    qtbot.addWidget(w)  # type: ignore[attr-defined]
    w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1100, 720)
    w.show()
    return w


def test_theme_qss_and_icons(qapp: object) -> None:  # QPixmap 은 QApplication 이 필요
    light, dark = build_qss("light"), build_qss("dark")
    assert "#F4F6F8" in light and "#0F1216" in dark and light != dark
    assert 'QPushButton[variant="primary"]' in light
    for n in names():
        assert not icon(n, "#000000", 16).isNull()


def test_state_recent_and_settings(state: AppState) -> None:
    state.add_recent(RecentEntry(path="D:/a", sensor="x", status="PASS", n_captures=3))
    state.add_recent(RecentEntry(path="D:/b", status="FAIL"))
    state.add_recent(RecentEntry(path="d:/A", status="WARN"))  # 대소문자 다른 같은 경로 → 갱신
    r = state.recent()
    assert [e.path for e in r] == ["d:/A", "D:/b"]
    state.remove_recent("D:/b")
    assert len(state.recent()) == 1
    state.theme = "dark"
    state.lang = "en"
    assert state.theme == "dark" and state.lang == "en"
    assert state.out_dir_for(Path("D:/data/site")).name == "site"
    state.out_mode = "inside"
    assert state.out_dir_for(Path("D:/data/site")).name == "flightqa_out"


def test_window_navigation_and_theme(window: MainWindow) -> None:
    assert window.stack.currentWidget() is window.home
    window.show_page("settings")
    assert window.stack.currentWidget() is window.settings
    window.show_page("home")
    before = current_mode()
    window.toggle_theme()
    assert current_mode() != before
    window.toggle_theme()
    assert current_mode() == before


def test_settings_preset_roundtrip(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("flightqa.gui.widgets.settings_page.presets_dir", lambda: tmp_path)
    sp = window.settings
    field = sp.fields["flight.target_agl_m"]
    field.setText("95")  # type: ignore[attr-defined]  # "auto" 기본값이라 QLineEdit
    cfg = sp.current_config()
    assert cfg.flight.target_agl_m == 95.0
    sp.preset_name.setText("테스트 100m")
    sp.save_preset()
    saved = tmp_path / "테스트_100m.toml"
    assert saved.exists() and "target_agl_m = 95.0" in saved.read_text(encoding="utf-8")
    sp.load_preset(None)
    assert sp.current_config().flight.target_agl_m == "auto"
    sp.load_preset(str(saved))
    assert sp.current_config().flight.target_agl_m == 95.0
    toml = sp.to_toml()
    assert "[flight]" in toml and "[rtk]" in toml and "fixed_flags = [50]" in toml


def test_banner_and_badge(qtbot: object) -> None:
    b = Banner()
    qtbot.addWidget(b)  # type: ignore[attr-defined]
    assert b.isHidden()
    b.show_message("fail", "문제", "trace")
    assert not b.isHidden() and b.detail_btn.isVisible() is not None
    badge = StatusBadge(Status.FAIL)
    assert badge.property("status") == "fail" and "실패" in badge.text()
    badge.set_status(Status.PASS, "OK")
    assert badge.property("status") == "pass" and "OK" in badge.text()


def test_label_cell_gate_badge(qtbot: object) -> None:
    """상세 표의 항목 셀: 게이트 항목은 보고서와 같은 GATE 배지, 긴 이름은 줄바꿈."""
    from PySide6.QtWidgets import QLabel, QWidget

    from flightqa.gui.widgets.results_page import _label_cell

    host = QWidget()  # 표의 셀처럼 부모가 크기를 강제한다 (최상위 창은 레이아웃 최소 크기에 막힌다)
    qtbot.addWidget(host)  # type: ignore[attr-defined]
    w = _label_cell("밴드 누락 캡처", True)
    w.setParent(host)
    badges = [x for x in w.findChildren(QLabel) if x.property("role") == "gate"]
    assert len(badges) == 1 and badges[0].text() == "GATE"
    host.resize(300, 80)
    host.show()
    w.setGeometry(0, 0, 260, 44)
    QApplication.processEvents()
    lb = w.label  # type: ignore[attr-defined]
    assert not lb.wordWrap()  # 한 줄에 들어가면 배지가 바로 옆
    w.setGeometry(0, 0, 90, 60)
    QApplication.processEvents()
    assert lb.wordWrap()  # 좁으면 이름만 줄바꿈
    plain = _label_cell("캡처 간격 (중앙값)", False)
    qtbot.addWidget(plain)  # type: ignore[attr-defined]
    assert not [x for x in plain.findChildren(QLabel) if x.property("role") == "gate"]


def test_hourglass_spinner_flips(qtbot: object) -> None:
    sp = HourglassSpinner(26)
    qtbot.addWidget(sp)  # type: ignore[attr-defined]
    assert (sp.active, sp.angle) == (False, 0.0)  # 튜플 비교: mypy 속성 축소 회피
    sp.start()
    assert sp.active
    # 정지 구간에서는 0°, 회전 구간을 지나면 180° 근처에서 다시 멈춘다
    for _ in range(int(sp.HOLD_MS / sp.FRAME_MS) - 2):
        sp._tick()
    assert sp.angle == 0.0
    for _ in range(int(sp.FLIP_MS / sp.FRAME_MS) + 3):
        sp._tick()
    assert 170.0 <= sp.angle <= 190.0
    for _ in range(int((sp.HOLD_MS + sp.FLIP_MS) / sp.FRAME_MS) + 1):
        sp._tick()
    assert sp.angle < 10.0 or sp.angle > 350.0  # 두 번 뒤집으면 원래 방향
    sp.stop()
    assert (sp.active, sp.angle) == (False, 0.0)  # 튜플 비교: mypy 속성 축소 회피
    sp.grab()  # paintEvent 가 예외 없이 그린다


def test_analysis_sensor_display_name(window: MainWindow, tmp_path: Path) -> None:
    from flightqa.core.models import DiscoveryResult, SensorKind

    page = window.analysis
    page._on_discovered(
        DiscoveryResult(root=tmp_path, sensor=SensorKind.DJI_M3M, confidence=1.0, image_files=[])
    )
    assert page.c_sensor.value.text() == "DJI Mavic 3 Multispectral"
    page._on_discovered(
        DiscoveryResult(root=tmp_path, sensor=SensorKind.REDEDGE_MX, confidence=0.9)
    )
    assert page.c_sensor.value.text() == "MicaSense RedEdge-MX"
    page._on_discovered(DiscoveryResult(root=tmp_path, sensor=None, confidence=0.0))
    assert page.c_sensor.value.text() == "감지 실패"


@pytest.mark.exiftool
def test_full_flow_and_results(window: MainWindow, synth_clean: BuildResult, qtbot: object) -> None:
    win = window
    with qtbot.waitSignal(win.analysis.finished, timeout=120_000):  # type: ignore[attr-defined]
        win.enqueue([synth_clean.root])
    assert win.analysis.spinner.isHidden() and not win.analysis.spinner.active
    assert win.analysis.title.text() == "완료"
    # 끝난 뒤에는 취소 버튼이 사라지고 '결과 보기' 가 나온다
    assert win.analysis.cancel_btn.isHidden() and not win.analysis.results_btn.isHidden()
    win.show_page("analysis")
    win.analysis.results_btn.click()
    assert win.stack.currentWidget() is win.results
    assert win.stack.currentWidget() is win.results
    assert win.results.report is not None
    assert win.results.report.status is Status.PASS
    assert "통과" in win.results.badge.text()
    assert win.results.grid.count() >= 6  # 항목 그룹 카드
    assert (win.state.out_dir_for(synth_clean.root) / "report.html").exists()
    assert (win.state.out_dir_for(synth_clean.root) / "summary.json").exists()
    recent = win.state.recent()
    assert recent and recent[0].status == "PASS" and recent[0].n_captures == synth_clean.n_captures
    # 상세 탭 표
    win.results.tabs.setCurrentIndex(1)
    assert win.results.table.rowCount() > 20
    # 비교: 자기 자신과 비교 → 모든 항목 동일
    win.results.compare.compare_with(win.state.out_dir_for(synth_clean.root) / "summary.json")
    assert win.results.compare.table.rowCount() > 20
    # 최근 결과 다시 열기
    win._open_recent_result(recent[0])
    assert win.results.report is not None


@pytest.mark.exiftool
def test_cancel_within_one_second(window: MainWindow, synth_m2: BuildResult, qtbot: object) -> None:
    win = window
    win.state.use_cache = False
    started = {"t": 0.0}

    def on_progress(stage: str, *_a: object) -> None:
        if stage == "extract" and not started["t"]:
            started["t"] = time.perf_counter()
            win.analysis.cancel()

    win.enqueue([synth_m2.root])
    assert win.analysis.worker is not None
    win.analysis.worker.progress.connect(on_progress)
    with qtbot.waitSignal(win.analysis.cancelled, timeout=30_000):  # type: ignore[attr-defined]
        pass
    elapsed = time.perf_counter() - started["t"]
    assert elapsed < 1.0, f"취소 반응 {elapsed:.2f}s"
    assert not win.analysis.running
    assert win.queue == []


def test_drop_dirs_parsing(tmp_path: Path) -> None:
    from PySide6.QtCore import QMimeData, QUrl

    from flightqa.gui.widgets.common import DropZone

    f = tmp_path / "a.tif"
    f.write_bytes(b"x")
    (tmp_path / "d").mkdir()
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(f)),
            QUrl.fromLocalFile(str(tmp_path / "d")),
            QUrl.fromLocalFile(str(tmp_path / "d")),
        ]
    )
    dirs = DropZone._dirs(mime)
    assert dirs == [tmp_path, tmp_path / "d"]


def test_escape_when_idle(window: MainWindow, qtbot: object) -> None:
    qtbot.keyClick(window, Qt.Key.Key_Escape)  # type: ignore[attr-defined]
    assert not window.analysis.running


def test_postprocess_page(window: MainWindow, tmp_path: Path, qtbot: object) -> None:
    """후처리 품질 화면: 합성 Metashape PDF 를 놓으면 표·판정·그룹이 나오고 내보내기가 된다."""
    from fixtures.metashape_text import make_pdf, ms_report_lines, rgb_report_lines
    from flightqa.gui.widgets.results_page import GroupBlock

    win = window
    win.show_page("postprocess")
    assert win.stack.currentWidget() is win.postprocess
    page = win.postprocess
    assert page.flight_combo.count() == 1 and page.flight_combo.currentData() == ""
    rgb = make_pdf(tmp_path / "rgb.pdf", rgb_report_lines())
    ms = make_pdf(tmp_path / "ms.pdf", ms_report_lines())
    (tmp_path / "other.pdf").write_bytes(b"%PDF-1.4 broken")
    page.load_paths([rgb, ms, tmp_path / "other.pdf"])
    assert page.table.rowCount() == 2 and len(page.items) == 2
    assert not page.banner.isHidden() and "1개 실패" in page.banner.text_lb.text()

    def cell(r: int, c: int) -> str:
        it = page.table.item(r, c)
        assert it is not None
        return it.text()

    assert cell(0, 0) == "rgb.pdf"
    assert cell(1, 2).startswith("Blue, Green, Red")
    assert cell(1, 3) == "1,540 (1,520)"
    # 마지막 행이 선택돼 판정·그룹이 보인다
    assert page.badge.text().strip() and page.headline.text() == "조건부 사용 가능"
    assert page.groups_box.count() >= 4
    first = page.groups_box.itemAt(0)
    assert first is not None and isinstance(first.widget(), GroupBlock)
    assert "ms.pdf" in page.meta_lb.text()
    page.table.selectRow(0)
    assert "rgb.pdf" in page.meta_lb.text()
    # 같은 파일은 다시 넣지 않는다
    page.load_paths([rgb])
    assert page.table.rowCount() == 2 and "이미" in page.banner.text_lb.text()
    # 내보내기 함수 (대화상자 없이)
    from flightqa.report import export

    written = export.write_metashape(page.items[0].result, tmp_path / "out", stem="rgb")
    assert all(p.exists() for p in written)
    page.clear()
    assert page.table.rowCount() == 0 and not page.export_btn.isEnabled()
    assert page.headline.text().startswith("보고서를 불러오면")


def test_splash_card(window: MainWindow, qtbot: object) -> None:
    """시작 카드: 상태 문구를 바꾸고 메인 창이 뜨면 닫힌다."""
    from flightqa.gui.splash import HEIGHT, WIDTH, Splash

    sp = Splash()
    qtbot.addWidget(sp)  # type: ignore[attr-defined]
    sp.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    sp.show()
    assert sp.size().width() == WIDTH and sp.size().height() == HEIGHT
    sp.set_status("창을 준비하는 중…")
    assert sp.status.text() == "창을 준비하는 중…"
    assert sp.bar.minimum() == 0 and sp.bar.maximum() == 0  # 진행 중 표시
    assert not sp.grab().isNull()
    sp.finish(window)
    assert sp.isHidden()
