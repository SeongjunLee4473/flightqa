"""임무 경로 화면: 조정기 임무 파일(KMZ/KML)을 불러와 요약·지도로 보고 QGIS 용으로 내보낸다.

분석과 독립된 도구다. 파일이나 폴더를 드롭존에 놓으면 ``core.mission`` 이 읽고, 표에 파라미터가
나오며, 아래 지도(보고서 지도와 같은 Plotly 지도: UTM/위경도 전환, 휠 확대, 위성 배경)가 폴리곤과
웨이포인트 순서를 그린다. 여러 임무를 불러오면 기본은 전부 보이는 범위, 행을 고르면 그 임무로
확대한다. 내보내기는 임무마다 GeoPackage + GeoJSON (``report.geo.write_missions_gis``).
WebEngine 을 쓸 수 없으면(테스트·offscreen) QPainter 미리보기로 대신한다.
"""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Callable
from datetime import timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shapely.geometry import MultiPolygon, Polygon, shape

from flightqa.core.mission import (
    MISSION_EXTENSIONS,
    MissionError,
    find_mission_files,
    load_mission,
    polygon_area_m2,
)
from flightqa.core.models import MissionPlan
from flightqa.gui.theme import current_mode, current_palette
from flightqa.gui.widgets.common import (
    Banner,
    FileDropZone,
    button,
    home_dir,
    label,
    section,
)
from flightqa.report import geo
from flightqa.report.mission_map import write_mission_map

COLUMNS = [
    "파일",
    "유형",
    "면적 (ha)",
    "고도 (m)",
    "종/횡중복 (%)",
    "여유 (m)",
    "속도 (m/s)",
    "방향 (°)",
    "웨이포인트",
    "경로 (m)",
    "예상 (분)",
    "지형추종",
    "생성 (KST)",
]
KST = timezone(timedelta(hours=9))
"""표시용 한국표준시. 파일의 createTime 은 UTC ms epoch, GeoPackage 속성은 UTC ISO 그대로."""
MAP_HEIGHT = 540


def _fmt(v: float | int | None, digits: int = 0) -> str:
    if v is None:
        return "—"
    return f"{v:,.{digits}f}"


class MissionPreview(QWidget):
    """QPainter 미리보기 (WebEngine 을 쓸 수 없을 때의 대체). 북쪽 위, 위도 보정 근사, 축척 막대."""

    def __init__(self) -> None:
        super().__init__()
        self.plans: list[MissionPlan] = []
        self.selected = -1
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("임무 미리보기")

    def set_plans(self, plans: list[MissionPlan], selected: int = -1) -> None:
        self.plans = list(plans)
        self.selected = selected
        self.update()

    def _bounds(self) -> tuple[float, float, float, float] | None:
        """선택한 임무가 있으면 그 임무에, 없으면 전체에 맞춘다 (임무끼리 km 단위로 떨어질 때)."""
        xs: list[float] = []
        ys: list[float] = []
        focus = [self.plans[self.selected]] if 0 <= self.selected < len(self.plans) else self.plans
        for p in focus:
            for g in (p.polygon_geojson, p.waypoints_geojson):
                if g:
                    b = shape(g).bounds
                    xs += [b[0], b[2]]
                    ys += [b[1], b[3]]
        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    def paintEvent(self, _e: QPaintEvent) -> None:
        pal = current_palette()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.fillRect(self.rect(), QColor(pal.bg2))
        qp.setPen(QPen(QColor(pal.border), 1))
        qp.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        b = self._bounds()
        if b is None:
            qp.setPen(QColor(pal.text3))
            qp.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "임무 파일을 불러오면 여기에 그려집니다"
            )
            qp.end()
            return
        minx, miny, maxx, maxy = b
        lat0 = (miny + maxy) / 2.0
        kx = 111_320.0 * math.cos(math.radians(lat0))
        ky = 111_320.0
        w_m = max((maxx - minx) * kx, 1.0)
        h_m = max((maxy - miny) * ky, 1.0)
        pad = 28.0
        sx = (self.width() - 2 * pad) / w_m
        sy = (self.height() - 2 * pad) / h_m
        s = min(sx, sy)
        ox = pad + ((self.width() - 2 * pad) - w_m * s) / 2.0
        oy = pad + ((self.height() - 2 * pad) - h_m * s) / 2.0

        def pt(lon: float, lat: float) -> QPointF:
            return QPointF(ox + (lon - minx) * kx * s, oy + (maxy - lat) * ky * s)

        brand = QColor(pal.brand)
        for i, plan in enumerate(self.plans):
            geom = shape(plan.polygon_geojson)
            polys: list[Polygon] = (
                list(geom.geoms)
                if isinstance(geom, MultiPolygon)
                else [geom]
                if isinstance(geom, Polygon)
                else []
            )
            fill = QColor(brand)
            fill.setAlpha(60 if i == self.selected else 22)
            qp.setBrush(fill)
            qp.setPen(QPen(brand, 2.2 if i == self.selected else 1.4))
            for poly in polys:
                qp.drawPolygon(QPolygonF([pt(x, y) for x, y in poly.exterior.coords]))
            if plan.waypoints_geojson:
                wps = [pt(x, y) for x, y in shape(plan.waypoints_geojson).coords]
                pen = QPen(QColor(pal.text2), 1.4)
                pen.setStyle(Qt.PenStyle.DashLine)
                qp.setPen(pen)
                qp.setBrush(Qt.BrushStyle.NoBrush)
                for a, c in pairwise(wps):
                    qp.drawLine(a, c)
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(QColor(pal.text2))
                for q in wps:
                    qp.drawEllipse(q, 2.2, 2.2)
                qp.setBrush(QColor(pal.pass_))
                qp.drawEllipse(wps[0], 5.0, 5.0)
                qp.setBrush(QColor(pal.fail))
                qp.drawEllipse(wps[-1], 5.0, 5.0)
        qp.setPen(QColor(pal.text3))
        qp.drawText(QRectF(self.width() - 60, 8, 52, 18), Qt.AlignmentFlag.AlignRight, "N ↑")
        bar_m = _nice_length(w_m / 4.0)
        bar_px = bar_m * s
        y = self.height() - 14
        qp.setPen(QPen(QColor(pal.text2), 2))
        qp.drawLine(QPointF(pad, y), QPointF(pad + bar_px, y))
        qp.setPen(QColor(pal.text3))
        qp.drawText(QRectF(pad, y - 18, 140, 16), Qt.AlignmentFlag.AlignLeft, f"{bar_m:g} m")
        qp.end()


def _nice_length(v: float) -> float:
    if v <= 0:
        return 100.0
    exp = math.floor(math.log10(v))
    base = 10.0**exp
    for m in (1, 2, 5):
        if m * base >= v:
            return m * base
    return 10 * base


class MissionMapView(QWidget):
    """임무 지도: WebEngine 이 있으면 Plotly 지도(HTML), 없으면 QPainter 미리보기."""

    def __init__(self, report_cfg: Callable[[], dict[str, Any] | None] | None = None) -> None:
        super().__init__()
        self._report_cfg = report_cfg
        self.plans: list[MissionPlan] = []
        self.selected = -1
        self.web: QWidget | None = None
        self.fallback: MissionPreview | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        """지도 HTML 임시 폴더. 위젯이 사라지거나 프로세스가 끝나면 함께 지워진다 (누수 방지)."""
        self.setFixedHeight(MAP_HEIGHT)
        self.setAccessibleName("임무 지도")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        if not os.environ.get("FLIGHTQA_NO_WEBVIEW"):
            try:
                from PySide6.QtWebEngineCore import QWebEngineSettings
                from PySide6.QtWebEngineWidgets import QWebEngineView

                view = QWebEngineView()
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
                )
                # 위성 배경(온라인 타일)이 file:// 페이지에서도 열리도록 (보고서 웹뷰와 같다)
                view.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
                )
                self.web = view
            except Exception:  # noqa: BLE001 - WebEngine 없음 → 대체 미리보기
                self.web = None
        if self.web is not None:
            lay.addWidget(self.web)
        else:
            self.fallback = MissionPreview()
            lay.addWidget(self.fallback)
        self.set_plans([], -1)

    @property
    def uses_web(self) -> bool:
        return self.web is not None

    def set_plans(self, plans: list[MissionPlan], selected: int = -1) -> None:
        """목록이 바뀌면 지도를 다시 만든다 (선택은 ``select`` 로 JS 만 호출)."""
        self.plans = list(plans)
        self.selected = selected
        if self.fallback is not None:
            self.fallback.set_plans(self.plans, selected)
            return
        if self._tmpdir is None:
            self._tmpdir = tempfile.TemporaryDirectory(prefix="flightqa_mission_")
        cfg = self._report_cfg() if self._report_cfg is not None else None
        html = write_mission_map(
            self.plans,
            Path(self._tmpdir.name) / "mission_map.html",
            selected,
            report_cfg=cfg,
            theme=current_mode(),
        )
        loader = getattr(self.web, "load", None)
        if callable(loader):
            loader(QUrl.fromLocalFile(str(html)))

    def select(self, index: int) -> None:
        self.selected = index
        if self.fallback is not None:
            self.fallback.set_plans(self.plans, index)
            return
        self._js(f"window.__fq_select&&window.__fq_select({int(index)})")

    def apply_theme(self) -> None:
        if self.fallback is not None:
            self.fallback.update()
            return
        self._js(f"window.__flightqa_setTheme&&window.__flightqa_setTheme('{current_mode()}')")

    def _js(self, code: str) -> None:
        page = getattr(self.web, "page", None)
        if callable(page):
            page().runJavaScript(code)


class MissionPage(QWidget):
    plans_changed = Signal(int)

    def __init__(self, report_cfg: Callable[[], dict[str, Any] | None] | None = None) -> None:
        super().__init__()
        self.plans: list[MissionPlan] = []
        self._paths: list[Path] = []
        # 화면 전체가 하나의 스크롤 (표·지도가 길어져도 끊기지 않게)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.page_scroll)
        content = QWidget()
        self.page_scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(12)
        root.addWidget(label("임무 경로", "title"))
        root.addWidget(
            label(
                "조정기(DJI Pilot 2)에서 내보낸 임무 파일(KMZ/KML)을 불러와 폴리곤·웨이포인트·"
                "비행 파라미터를 확인하고 QGIS 에서 여는 GeoPackage/GeoJSON 으로 내보냅니다. "
                "분석과 별개인 도구이며 데이터 폴더에 둔 같은 파일은 조사 구역 판정에도 쓰입니다.",
                "muted",
                wrap=True,
            )
        )
        self.drop = FileDropZone(
            "임무 파일(KMZ/KML)을 끌어다 놓거나 클릭해 선택",
            "DJI Pilot 2 비행 경로 라이브러리에서 내보낸 파일 · 폴더를 놓으면 안의 KMZ/KML 을 찾음",
            suffixes=MISSION_EXTENSIONS,
            icon_name="layers",
        )
        self.drop.files_dropped.connect(self.load_paths)
        self.drop.clicked.connect(self.choose_files)
        root.addWidget(self.drop)

        row = QHBoxLayout()
        row.addWidget(
            button("파일 선택…", variant="primary", icon_name="file", on_click=self.choose_files)
        )
        row.addWidget(
            button("폴더에서 찾기…", icon_name="folder-open", on_click=self.choose_folder)
        )
        row.addStretch(1)
        self.export_btn = button(
            "QGIS용 내보내기…", variant="primary", icon_name="download", on_click=self.export
        )
        self.export_btn.setEnabled(False)
        row.addWidget(self.export_btn)
        self.clear_btn = button("비우기", variant="danger", icon_name="trash", on_click=self.clear)
        row.addWidget(self.clear_btn)
        root.addLayout(row)
        self.banner = Banner()
        root.addWidget(self.banner)

        root.addWidget(
            section("불러온 임무", sub="행을 선택하면 지도가 그 임무로 확대 · 내보내기는 목록 전체")
        )
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("불러온 임무 표")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self._fit_table()
        root.addWidget(self.table)

        self.fit_all_btn = button("전체 보기", variant="ghost", on_click=self._fit_all)
        root.addWidget(
            section(
                "지도",
                action=self.fit_all_btn,
                sub=(
                    "UTM(m)·위경도 전환 · 휠 확대 · 위성 배경 · "
                    "폴리곤(보라)·웨이포인트(점선), 시작 초록 · 끝 빨강"
                ),
            )
        )
        self.map = MissionMapView(report_cfg)
        self.fit_all_btn.setVisible(not self.map.uses_web)  # 웹 지도에는 같은 버튼이 안에 있다
        root.addWidget(self.map)
        self.hint = QLabel("")
        self.hint.setProperty("role", "muted")
        root.addWidget(self.hint)
        root.addStretch(1)

    # ---- 불러오기
    def choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "임무 파일 선택", home_dir(), "임무 파일 (*.kmz *.kml);;모든 파일 (*)"
        )
        if files:
            self.load_paths([Path(f) for f in files])

    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "임무 파일이 있는 폴더", home_dir())
        if d:
            self.load_paths([Path(d)])

    def load_paths(self, paths: list[Path]) -> None:
        files: list[Path] = []
        for p in paths:
            if p.is_dir():
                files.extend(find_mission_files(p))
            elif p.suffix.lower() in MISSION_EXTENSIONS:
                files.append(p)
        if not files:
            self.banner.show_message("warn", "KMZ/KML 파일이 없습니다.")
            return
        added, errors = 0, []
        for f in files:
            rp = f.resolve()
            if rp in self._paths:
                continue
            try:
                plan = load_mission(rp)
            except MissionError as e:
                errors.append(str(e))
                continue
            self.plans.append(plan)
            self._paths.append(rp)
            added += 1
        self._refresh()
        if errors:
            self.banner.show_message(
                "fail" if not added else "warn",
                f"{added}개 불러옴, {len(errors)}개 실패",
                "\n".join(errors),
            )
        elif added:
            self.banner.show_message(
                "pass", f"임무 {added}개를 불러왔습니다 (총 {len(self.plans)}개)."
            )
        else:
            self.banner.show_message("warn", "이미 불러온 파일입니다.")
        self.plans_changed.emit(len(self.plans))

    def clear(self) -> None:
        self.plans.clear()
        self._paths.clear()
        self._refresh()
        self.banner.hide()
        self.plans_changed.emit(0)

    def apply_theme(self) -> None:
        self.map.apply_theme()

    def _refresh(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for plan in self.plans:
            r = self.table.rowCount()
            self.table.insertRow(r)
            area = polygon_area_m2(plan)
            vals = [
                plan.source,
                plan.template_type or "—",
                _fmt(area / 10_000.0 if area is not None else None, 2),
                _fmt(plan.height_m, 1),
                f"{_fmt(plan.overlap_h_pct)} / {_fmt(plan.overlap_w_pct)}",
                _fmt(plan.margin_m, 0),
                _fmt(plan.speed_mps, 1),
                _fmt(plan.direction_deg, 0),
                str(plan.n_waypoints),
                _fmt(plan.distance_m, 0),
                _fmt(plan.duration_s / 60.0 if plan.duration_s is not None else None, 1),
                "예" if plan.elevation_optimize else "아니오",
                plan.created_utc.astimezone(KST).strftime("%Y-%m-%d %H:%M")
                if plan.created_utc
                else "—",
            ]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c not in (0, 1, 11, 12):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, it)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.export_btn.setEnabled(bool(self.plans))
        self._fit_table()
        # 하나면 그 임무에 맞추고, 여럿이면 전부 보이는 범위가 기본 (행을 고르면 확대)
        sel = 0 if len(self.plans) == 1 else -1
        if sel == 0:
            self.table.selectRow(0)
        self.map.set_plans(self.plans, sel)
        self.hint.setText(
            "임무 하나를 보려면 표에서 행을 선택하세요. 다시 전부 보려면 [전체 보기]."
            if len(self.plans) > 1
            else ""
        )

    def _fit_table(self) -> None:
        """헤더 + 행(최대 5) + 가로 스크롤바 높이로 고정. 빈 표도 헤더와 한 행 자리는 보인다."""
        rows = max(1, min(self.table.rowCount(), 5))
        h = (
            self.table.horizontalHeader().height()
            + rows * self.table.verticalHeader().defaultSectionSize()
            + self.table.horizontalScrollBar().sizeHint().height()
            + 4
        )
        self.table.setFixedHeight(h)

    def _selection_changed(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        self.map.select(next(iter(rows)) if rows else -1)

    def _fit_all(self) -> None:
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.map.select(-1)

    # ---- 내보내기
    def export(self) -> None:
        if not self.plans:
            self.banner.show_message("warn", "먼저 임무 파일을 불러오세요.")
            return
        d = QFileDialog.getExistingDirectory(self, "QGIS용 파일을 저장할 폴더", home_dir())
        if not d:
            return
        written = geo.write_missions_gis(self.plans, Path(d))
        self.banner.show_message(
            "pass",
            f"{len(written)}개 파일을 {d} 에 저장했습니다. GeoPackage 를 QGIS 에 끌어다 놓으면 "
            "mission_polygon · mission_waypoints 레이어로 열립니다.",
        )
