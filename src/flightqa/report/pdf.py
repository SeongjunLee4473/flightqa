"""HTML 보고서 → PDF (CLAUDE.md §5.2).

엔진: PySide6 QtWebEngine (Chromium). 근거 (docs/ASSUMPTIONS.md M3):
- Plotly 차트가 JS 로 그려지므로 JS 를 실행하는 엔진이 필요하다 (weasyprint 는 불가).
- Playwright 는 Chromium 150 MB 별도 다운로드가 필요해 오프라인 exe 배포에 불리하다.
- QtWebEngine 은 GUI 용 PySide6 에 이미 들어 있고 시스템 한글 폰트를 그대로 임베드한다.

Qt 는 메인 스레드에서만 돌려야 한다. GUI 에서는 기존 QApplication 을 재사용하고, CLI 에서는 여기서
만든다.
스크린샷(``html_to_png``)은 보고서 레이아웃 검증(CLAUDE.md §11)에 쓴다.
"""

from __future__ import annotations

import atexit
import contextlib
import gc
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_MS = 90_000


def _prepare_env() -> None:
    """플랫폼 플러그인 선택.

    Windows 는 항상 데스크톱 세션이 있으므로 네이티브 플랫폼을 쓰고 창은
    ``WA_DontShowOnScreen`` 으로 숨긴다. ``offscreen`` 플랫폼은 렌더는 되지만 QtWebEngine
    종료 시 접근 위반(0xC0000005) 이 나는 것을
    실측했다 (docs/ASSUMPTIONS.md M3). Linux/macOS 에서 디스플레이가 없을 때만 offscreen 으로 간다.
    """
    if sys.platform != "win32" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--disable-gpu" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (flags + " --disable-gpu").strip()
    os.environ.setdefault("QT_LOGGING_RULES", "qt.webenginecontext.debug=false")


_APP: object = None
"""헤드리스 렌더용 QApplication.

인터프리터 종료 때 Qt/Chromium 객체가 무작위 순서로 파괴되면 세그폴트(exit 139)가 나므로,
여기서 만든 앱은 전역으로 잡아 두고 ``atexit`` 에서 명시적으로 정리한다.
"""


def _shutdown() -> None:
    global _APP
    app = _APP
    if app is None:
        return
    try:
        app.processEvents()  # type: ignore[attr-defined]
        app.quit()  # type: ignore[attr-defined]
        app.processEvents()  # type: ignore[attr-defined]
    finally:
        _APP = None
        del app
        gc.collect()


def _application() -> object:
    global _APP
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        _APP = app = QApplication(sys.argv[:1])
        atexit.register(_shutdown)
    return app


def _render(
    html_path: Path,
    finish: Callable[[object, object, Callable[[bool], None]], None],
    timeout_ms: int,
    viewport: tuple[int, int],
    *,
    ready_flag: str = "__flightqa_ready",
    max_tries: int = 100,
) -> bool:
    """공통 렌더 루프: 페이지 로드 → 준비 신호(``window.<ready_flag>``) 대기 → finish 콜백.

    화면 캡처는 차트가 그려진 ``__flightqa_ready`` 를, PDF 는 인쇄용 정적 이미지까지 구운
    ``__flightqa_print_ready`` 를 기다린다 (0.1 s 간격, 최대 ``max_tries`` 회).
    ``app.exec()`` 대신 중복 ``QEventLoop`` 로 기다리므로 GUI 안에서도, CLI 에서도 같은 코드가 돈다.
    """
    _prepare_env()
    from PySide6.QtCore import QEventLoop, QSize, Qt, QTimer, QUrl
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = _application()
    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)  # 화면에 창을 띄우지 않는다
    view.settings().setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    view.settings().setAttribute(  # [report] online_basemap = true 인 보고서의 타일 로드
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )
    view.resize(QSize(*viewport))
    loop = QEventLoop()
    result = {"ok": False, "done": False}

    def done(ok: bool) -> None:
        if result["done"]:
            return
        result["ok"] = ok
        result["done"] = True
        loop.quit()

    def poll_ready(tries: int = 0) -> None:
        def got(v: object) -> None:
            if v is True or tries > max_tries:
                if v is not True:
                    log.warning("보고서 준비 신호(%s) 대기 초과 — 현재 상태로 진행", ready_flag)
                finish(view, view.page(), done)
            else:
                QTimer.singleShot(100, lambda: poll_ready(tries + 1))

        view.page().runJavaScript(f"window.{ready_flag} === true", got)

    def on_load(ok: bool) -> None:
        if not ok:
            log.error("보고서 HTML 로드 실패: %s", html_path)
            done(False)
            return
        QTimer.singleShot(50, poll_ready)

    view.loadFinished.connect(on_load)
    QTimer.singleShot(timeout_ms, lambda: done(False))
    view.load(QUrl.fromLocalFile(str(html_path.resolve())))
    view.show()
    loop.exec()
    # 뷰를 명시적으로 정리하고 이벤트를 소화해 파괴 순서를 고정한다.
    view.hide()
    view.setParent(None)
    view.deleteLater()
    for _ in range(3):
        app.processEvents()  # type: ignore[attr-defined]
    return bool(result["ok"])


PDF_PAGE_MARGIN_MM = 12.0
"""템플릿의 ``@page{margin:12mm}`` 와 같아야 인쇄용 이미지 폭(703 px) 계산이 맞는다."""


def html_to_pdf(html_path: Path, pdf_path: Path, *, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
    """A4 세로, 12 mm 여백. 성공 여부를 돌려준다 (예외로 파이프라인을 막지 않는다).

    보고서 JS 가 A4 폭에 맞춰 차트를 정적 PNG 로 구워 둔 뒤(``__flightqa_print_ready``) 인쇄하므로
    브라우저의 인쇄(Ctrl+P) 와 같은 결과가 나온다.
    """
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    def finish(_view: object, page: object, done: Callable[[bool], None]) -> None:
        m = PDF_PAGE_MARGIN_MM
        layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(m, m, m, m),
        )
        page.pdfPrintingFinished.connect(lambda _p, ok: done(bool(ok)))  # type: ignore[attr-defined]
        page.printToPdf(str(pdf_path), layout)  # type: ignore[attr-defined]

    return _render(
        html_path,
        finish,
        timeout_ms,
        (1280, 1400),
        ready_flag="__flightqa_print_ready",
        max_tries=600,  # 차트 수 × toImage(3 배 해상도) — 넉넉히 60 s
    )


def html_to_png(
    html_path: Path,
    png_path: Path,
    *,
    width: int = 1280,
    height: int = 900,
    full_page: bool = False,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> bool:
    """뷰포트 스크린샷 (레이아웃 검증·문서용).

    ``full_page`` 면 뷰포트 높이만큼 스크롤하며 조각을 찍어 세로로 이어붙인다. 뷰포트를 문서 높이로
    늘려 한 번에 찍는 방식은 수천 px 이상에서 아래쪽이 비어 나오는 것을 실측해 버렸다.
    고정 툴바는 첫 조각에만 보이도록 숨긴다.
    """
    png_path.parent.mkdir(parents=True, exist_ok=True)

    def finish(view: object, page: object, done: Callable[[bool], None]) -> None:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QImage, QPainter

        if not full_page:
            pix = view.grab()  # type: ignore[attr-defined]
            done(bool(pix.save(str(png_path), "PNG")))
            return

        state: dict[str, Any] = {"y": 0, "parts": []}

        def wait_paint(cb: Callable[[], None], tries: int = 0) -> None:
            def painted(p: object) -> None:
                if p is True or tries > 50:
                    # 렌더 프로세스의 프레임이 위젯까지 오는 데 시간이 더 걸린다 (실측: 150 ms 부족)
                    QTimer.singleShot(500, cb)
                else:
                    QTimer.singleShot(100, lambda: wait_paint(cb, tries + 1))

            page.runJavaScript("window.__fq_painted === true", painted)  # type: ignore[attr-defined]

        def scroll_to(y: int, cb: Callable[[], None]) -> None:
            js = (
                f"window.scrollTo(0,{y});window.__fq_painted=false;"
                "requestAnimationFrame(function(){requestAnimationFrame(function(){"
                "window.__fq_painted=true;});});"
            )
            page.runJavaScript(js, lambda _v: wait_paint(cb))  # type: ignore[attr-defined]

        def stitch(doc_h: int) -> None:
            parts: list[QImage] = state["parts"]
            if not parts:
                done(False)
                return
            dpr = parts[0].width() / width
            out = QImage(parts[0].width(), int(doc_h * dpr), QImage.Format.Format_ARGB32)
            out.fill(0)
            painter = QPainter(out)
            y = 0
            for i, img in enumerate(parts):
                # 마지막 조각은 문서 끝에 맞춰 아래쪽 여백을 잘라낸다
                remain = int(doc_h * dpr) - y
                if img.height() > remain:
                    img = img.copy(0, img.height() - remain, img.width(), remain)
                painter.drawImage(0, y, img)
                y += img.height()
                if i == 0:
                    page.runJavaScript(  # type: ignore[attr-defined]
                        "document.querySelector('.toolbar')&&"
                        "(document.querySelector('.toolbar').style.visibility='')"
                    )
            painter.end()
            done(bool(out.save(str(png_path))))

        def got(v: object) -> None:
            try:
                doc_h = min(int(float(str(v))), 40000)
            except (TypeError, ValueError):
                doc_h = height
            step = height

            def capture() -> None:
                img = view.grab().toImage()  # type: ignore[attr-defined]
                state["parts"].append(img)
                if len(state["parts"]) == 1:
                    page.runJavaScript(  # type: ignore[attr-defined]
                        "document.querySelector('.toolbar')&&"
                        "(document.querySelector('.toolbar').style.visibility='hidden')"
                    )
                state["y"] += step
                if state["y"] >= doc_h:
                    stitch(doc_h)
                else:
                    scroll_to(min(state["y"], doc_h - step), capture)

            scroll_to(0, capture)

        page.runJavaScript("document.documentElement.scrollHeight", got)  # type: ignore[attr-defined]

    # 인쇄용 이미지 생성이 끝나 렌더 프로세스가 한가해진 뒤 찍는다
    return _render(
        html_path,
        finish,
        timeout_ms,
        (width, height),
        ready_flag="__flightqa_print_ready",
        max_tries=600,
    )


def measure_layout(
    html_path: Path, *, width: int = 1280, timeout_ms: int = DEFAULT_TIMEOUT_MS
) -> dict[str, float]:
    """문서 폭·높이와 가로 오버플로 여부를 실측한다 (CLAUDE.md §11)."""
    out: dict[str, float] = {}

    def finish(_view: object, page: object, done: Callable[[bool], None]) -> None:
        js = (
            "JSON.stringify({sw: document.documentElement.scrollWidth,"
            " cw: document.documentElement.clientWidth,"
            " sh: document.documentElement.scrollHeight,"
            # .chart 로 한정: Plotly.toImage 가 잠깐 만드는 임시 div 를 세지 않는다
            " charts: document.querySelectorAll('.chart.js-plotly-plot').length,"
            " overflow: Array.from(document.querySelectorAll('body *')).filter("
            "e => e.getBoundingClientRect().right > "
            "document.documentElement.clientWidth + 1).length})"
        )

        def got(v: object) -> None:
            import json

            with contextlib.suppress(TypeError, ValueError):
                out.update({k: float(x) for k, x in json.loads(str(v)).items()})
            done(True)

        page.runJavaScript(js, got)  # type: ignore[attr-defined]

    _render(html_path, finish, timeout_ms, (width, 900))
    return out
