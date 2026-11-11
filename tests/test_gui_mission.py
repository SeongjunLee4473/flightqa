"""임무 경로 화면(MissionPage), 파일 드롭존, 확인 대화상자(ConfirmDialog)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QDialog

from flightqa.gui.main_window import PAGES, MainWindow
from flightqa.gui.widgets.common import ConfirmDialog, FileDropZone
from flightqa.gui.widgets.mission_page import COLUMNS, MissionPage, _nice_length
from test_gui import state, window  # noqa: F401  # 픽스처 재사용
from test_mission import SQUARE, make_kmz


def test_mission_page_load_table_preview_export(qtbot: object, tmp_path: Path) -> None:
    page = MissionPage()
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    page.resize(1000, 800)
    assert not page.export_btn.isEnabled()
    k1 = make_kmz(
        tmp_path / "임무A.kmz", SQUARE, [(127.0, 37.0), (127.001, 37.0), (127.001, 37.001)]
    )
    k2 = make_kmz(tmp_path / "임무B.kmz", [(lo + 0.01, la) for lo, la in SQUARE])
    (tmp_path / "sub").mkdir()
    k3 = make_kmz(tmp_path / "sub" / "임무C.kmz", SQUARE)
    bad = tmp_path / "bad.kmz"
    bad.write_bytes(b"nope")
    page.load_paths([k1, k2, bad])
    assert len(page.plans) == 2 and page.table.rowCount() == 2
    assert page.table.columnCount() == len(COLUMNS)
    c0, c8 = page.table.item(0, 0), page.table.item(0, 8)
    assert c0 is not None and c0.text() == "임무A.kmz"
    assert c8 is not None and c8.text() == "3"  # 웨이포인트 수
    assert "1개 실패" in page.banner.text_lb.text()
    assert page.export_btn.isEnabled()
    # 같은 파일은 다시 안 들어가고, 폴더를 주면 안을 뒤진다
    page.load_paths([k1])
    assert len(page.plans) == 2
    page.load_paths([tmp_path])
    assert {p.source for p in page.plans} == {"임무A.kmz", "임무B.kmz", "임무C.kmz"}
    assert k3.exists()
    # 웹뷰가 꺼진 테스트에서는 QPainter 미리보기가 대신하고, 선택이 지도에 전달된다
    assert not page.map.uses_web and page.map.fallback is not None
    assert page.map.selected == -1  # 여럿이면 기본은 전체 보기
    page.table.selectRow(1)
    page.map.fallback.grab()
    assert page.map.selected == 1 and page.map.fallback.selected == 1
    page._fit_all()
    assert page.map.selected == -1
    page.map.fallback.set_plans([], -1)
    page.map.fallback.grab()
    # 내보내기 (대화상자 대신 직접)
    from flightqa.report import geo

    written = geo.write_missions_gis(page.plans, tmp_path / "gis")
    assert {p.suffix for p in written} == {".gpkg", ".geojson"}
    page.clear()
    assert page.plans == [] and page.table.rowCount() == 0 and not page.export_btn.isEnabled()


def test_nice_length() -> None:
    assert _nice_length(0) == 100.0
    assert _nice_length(37) == 50.0
    assert _nice_length(120) == 200.0
    assert _nice_length(800) == 1000.0


def test_file_dropzone_filters(qtbot: object, tmp_path: Path) -> None:
    z = FileDropZone("t", "h", suffixes={".kmz", ".kml"})
    qtbot.addWidget(z)  # type: ignore[attr-defined]
    (tmp_path / "a.kmz").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / "d").mkdir()
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(tmp_path / "a.kmz")),
            QUrl.fromLocalFile(str(tmp_path / "b.txt")),
            QUrl.fromLocalFile(str(tmp_path / "d")),
        ]
    )
    assert [p.name for p in z._paths(mime)] == ["a.kmz", "d"]
    z.refresh_icon()
    assert not z.icon_lb.pixmap().isNull()


def test_confirm_dialog_buttons(qtbot: object) -> None:
    dlg = ConfirmDialog(
        None, "제목", "본문", "상세", ok_text="진행", danger=True, icon_name="trash"
    )
    qtbot.addWidget(dlg)  # type: ignore[attr-defined]
    assert dlg.windowTitle() == "제목" and dlg.text_lb.text() == "본문"
    assert dlg.detail_lb.isVisibleTo(dlg) and dlg.ok_btn.text() == "진행"
    assert dlg.ok_btn.property("variant") == "danger"
    assert dlg.objectName() == "root" and "background" in dlg.styleSheet()
    dlg.ok_btn.click()
    assert dlg.result() == QDialog.DialogCode.Accepted
    dlg2 = ConfirmDialog(None, "t", "x", cancel_text=None)
    qtbot.addWidget(dlg2)  # type: ignore[attr-defined]
    assert not hasattr(dlg2, "cancel_btn") and not dlg2.detail_lb.isVisibleTo(dlg2)
    dlg2.grab()


def test_main_window_has_mission_page(window: MainWindow, tmp_path: Path) -> None:  # noqa: F811
    keys = [k for k, _, _ in PAGES]
    assert keys == ["home", "analysis", "results", "mission", "postprocess", "settings"]
    window.show_page("mission")
    assert window.stack.currentWidget() is window.mission
    assert window.nav["mission"].isChecked()
    # 창에 KMZ 만 놓으면 임무 화면이 받는다
    k = make_kmz(tmp_path / "m.kmz", SQUARE)
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QDropEvent

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(k))])
    window.show_page("home")
    ev = QDropEvent(
        QPoint(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(ev)
    assert window.stack.currentWidget() is window.mission
    assert [p.source for p in window.mission.plans] == ["m.kmz"]
    window.mission.clear()
