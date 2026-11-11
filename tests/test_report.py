"""M3 보고서 테스트.

HTML 렌더(ko/en)·자체 완결성·다국어·썸네일·차트 JSON, 그리고 QtWebEngine 실측 (PNG/PDF/레이아웃).
"""

from __future__ import annotations

import json
import re
import zlib
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pytest

from fixtures.synth import BuildResult, make_tiff
from flightqa.config.schema import load_config
from flightqa.core import pipeline
from flightqa.core.models import RunReport
from flightqa.report import charts
from flightqa.report import html as html_report
from flightqa.report.i18n import Translator
from flightqa.report.thumbs import encode_png_gray8, thumbnail_data_url
from flightqa.theme import tokens

DOCS_SHOTS = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


@pytest.fixture(scope="module")
def report_m2(synth_m2: BuildResult) -> RunReport:
    return pipeline.run(synth_m2.root, load_config(), use_cache=False)


def _strip_js(html: str) -> str:
    """plotly.js(4.8 MB) 블록을 떼어낸다.

    단언이 실패했을 때 pytest 가 거대 문자열의 diff 를 계산하느라 멈추지 않게 하고,
    plotly.js 안의 우연한 문자열("data:image/png" 등)이 검사에 섞이지 않게 한다.
    """
    i = html.find("<script>")
    j = html.find("</script>", i)
    assert i > 0 and j > i
    return html[:i] + "<script>/*plotly.js*/" + html[j:]


@pytest.fixture(scope="module")
def html_ko_full(report_m2: RunReport) -> str:
    return html_report.render_html(report_m2, "ko")


@pytest.fixture(scope="module")
def html_ko(html_ko_full: str) -> str:
    return _strip_js(html_ko_full)


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.ext: list[str] = []
        self.imgs = 0
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if d.get("id"):
            self.ids.append(str(d["id"]))
        for k in ("src", "href"):
            v = d.get(k)
            if v and v.startswith(("http://", "https://", "//")):
                self.ext.append(v)
        if tag == "img":
            self.imgs += 1
        if tag == "script":
            self.scripts += 1


# --------------------------------------------------------------------------- HTML


@pytest.mark.exiftool
def test_html_self_contained_and_structure(
    html_ko: str, html_ko_full: str, report_m2: RunReport
) -> None:
    c = _Collector()
    c.feed(html_ko)
    assert c.ext == []  # 외부 리소스 없음 (CLAUDE.md §5.1)
    assert len(html_ko_full) > 3_000_000 and "Plotly.newPlot" in html_ko  # plotly.js 인라인
    for section in (
        "h-verdict",
        "flight-0",
        "chart-0-map",
        "chart-0-mapll",
        "chart-0-alt",
        "chart-0-hist",
        "report-options",
        "h-excluded",
        "h-appendix",
    ):
        assert section in c.ids, section
    # 인쇄: 차트는 A4 폭에 맞춘 정적 PNG 로, 테마와 무관하게 라이트 팔레트로 찍는다
    assert "figure.has-print .print-img{display:block}" in html_ko
    assert "@media print{:root,:root[data-theme=dark]" in html_ko
    assert 'class="chart-legend"' in html_ko and "buildLegend" in html_ko
    assert 'class="chart-legend"' in html_ko and "buildLegend" in html_ko
    assert "__flightqa_print_ready" in html_ko
    assert (
        "server.arcgisonline.com" in html_ko
    )  # 배경지도 URL 은 옵션 JSON 에만 (외부 리소스 로드 없음)
    assert c.imgs >= 2  # 썸네일 (패널·포화·첫 캡처)
    assert "data:image/png;base64," in html_ko
    assert report_m2.headline in html_ko and report_m2.headline_detail in html_ko
    assert "종합 판정" in html_ko and "권고 조치" in html_ko
    # 판정·그룹 블록의 상태는 data-status (클래스 .fail 등은 글자색까지 바꾸므로 블록에 쓰지 않는다)
    assert 'class="verdict surface" data-status="' in html_ko
    assert html_ko.count('<div class="group" data-status="') == sum(
        len(f.groups) for f in report_m2.flights
    ) + len(report_m2.run_groups)
    assert "group-row" not in html_ko
    # 근거 열은 글머리표 목록 (근거 • / 권고 조치 → / 기준 ▸), 권고 조치은 구조화 목록
    assert 'class="td-list"' in html_ko and '<div class="t"><span>기준:' in html_ko
    assert ('<ul class="actions">' in html_ko) == bool(report_m2.action_items)
    assert "ResizeObserver" in html_ko and "ticklabelstandoff" in html_ko
    # GUI 웹뷰용 embedded 모드 (테마는 GUI 가 지정)
    assert "html.embedded #theme-btn{display:none}" in html_ko and "__flightqa_setTheme" in html_ko
    assert 'lang="ko"' in html_ko
    assert ":root{--bg:" in html_ko and "data-theme=dark" in html_ko
    assert "@media print" in html_ko and "size:A4" in html_ko
    for m in report_m2.flights[0].groups[0].metrics:
        assert m.label in html_ko


@pytest.mark.exiftool
def test_html_chart_interaction_and_legend_swatch(html_ko_full: str) -> None:
    """시계열도 휠 확대·이동·초기화가 되고, 마커 범례는 그래프 기호(x 등)를 따른다."""
    js = html_ko_full
    std = js[js.index("var STD_CFG=") : js.index("var STD_CFG=") + 400]
    assert (
        "scrollZoom:true" in std and "displayModeBar:true" in std and "doubleClick:'reset'" in std
    )
    assert "'zoom2d'" in std  # 상자 확대는 제거 → 실수 드래그로 확대되지 않음
    assert "fig.layout.dragmode='pan'" in js
    assert "function markerShape(" in js and "s==='x'" in js and "s==='square'" in js
    assert "el=markerShape(tr.marker&&tr.marker.symbol,color)" in js
    assert "chart.zoom_hint" not in js  # 번역이 채워졌다
    assert js.count("휠 확대 · 드래그 이동 · 더블클릭 또는 ⌂ 초기화") >= 4


def test_html_unified_map_logo_and_terms(html_ko_full: str) -> None:
    """지도는 한 장(좌표계 전환), 로고는 드론, 용어는 비행속도·밴드 누락 없음."""
    h = html_ko_full
    assert h.count('class="map-figure"') == 1  # 비행당 지도 figure 하나
    assert 'data-crs="utm"' in h and 'data-crs="wgs84"' in h and "data-fit" in h
    assert 'id="chart-0-mapll" hidden' in h  # 위경도 지도는 숨긴 채로 시작
    assert "function showCrs(" in h and "function visibleOf(" in h
    assert "c.isMap&&c.el.hidden" in h  # 숨은 좌표계는 인쇄 이미지를 만들지 않는다
    assert "legendrank" in h and "items.sort(" in h
    # 로고: 옛 맥박선 경로가 없고 드론(원 4개)이 상단·파비콘에 들어간다
    assert "M3 12h4l3-7 4 14 3-7h4" not in h
    assert h.count('<circle cx="5.5" cy="5.5" r="3"/>') >= 1 and 'rel="icon"' in h
    # 용어
    assert "대지속도" not in h and "비행속도" in h
    assert "밴드 누락 없음" in h and " 완전<" not in h
    assert '<span class="gate">GATE</span>' in h


def test_html_english(report_m2: RunReport) -> None:
    en = _strip_js(html_report.render_html(report_m2, "en", thumbnails=False))
    assert 'lang="en"' in en
    assert "Overall verdict" in en and "Forward overlap (measured median)" in en
    assert "Evidence sentences are shown in the original language" in en
    assert "data:image/png" not in en  # thumbnails=False


@pytest.mark.exiftool
def test_write_html(report_m2: RunReport, tmp_path: Path) -> None:
    p = html_report.write_html(report_m2, tmp_path / "r" / "report.html")
    assert p.exists() and p.stat().st_size > 3_000_000


def test_no_flights_report(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("x")
    rep = pipeline.run(tmp_path, load_config(), use_cache=False)
    html = _strip_js(html_report.render_html(rep, "ko"))
    assert "분석할 비행이 없습니다" in html


# --------------------------------------------------------------------------- 구성 요소


def test_i18n_fallback_and_labels() -> None:
    ko, en = Translator("ko"), Translator("en")
    assert ko("section.verdict") == "종합 판정"
    assert en("section.verdict") == "Overall verdict"
    assert ko("no.such.key") == "no.such.key"
    assert en.metric_label("flight.speed", "비행속도") == "Flight speed (survey-line median)"
    assert ko.metric_label("flight.speed", "비행속도") == "비행속도"
    assert en.status("N/A") == "N/A" and ko.status("WARN") == "주의"
    assert Translator("xx").lang == "ko"


def test_tokens_css() -> None:
    css = tokens.css_root()
    assert css.startswith(":root{--bg:#F4F6F8")
    assert "prefers-color-scheme: dark" in css and ":root[data-theme=dark]" in css
    assert "--pass:#1B7A3D" in css and "--font-ui:" in css
    assert set(tokens.plotly_colors(True)) == {
        "text",
        "grid",
        "brand",
        "pass",
        "warn",
        "fail",
        "na",
    }


def test_png_encoder_and_thumbnail(tmp_path: Path) -> None:
    img = np.arange(0, 12, dtype=np.uint8).reshape(3, 4)
    png = encode_png_gray8(img)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and png.endswith(b"IEND\xaeB`\x82")
    # IDAT 를 풀어 원본 바이트가 나오는지 (행마다 필터 바이트 0)
    idat_start = png.index(b"IDAT") + 4
    idat_len = int.from_bytes(png[idat_start - 8 : idat_start - 4], "big")
    raw = zlib.decompress(png[idat_start : idat_start + idat_len])
    assert raw == b"".join(b"\x00" + img[r].tobytes() for r in range(3))
    with pytest.raises(ValueError):
        encode_png_gray8(np.zeros((2, 2), dtype=np.uint16))
    p = make_tiff(tmp_path / "t.tif", width=640, height=480, saturated_fraction=0.01)
    url = thumbnail_data_url(p, max_width=160, sat_dn=65520)
    assert url is not None and url.startswith("data:image/png;base64,")
    assert thumbnail_data_url(tmp_path / "missing.tif") is None


@pytest.mark.exiftool
def test_charts_json(report_m2: RunReport) -> None:
    f = report_m2.flights[0]
    labels = {
        "track": "t",
        "coverage": "c",
        "hole": "h",
        "event": "e",
        "target": "g",
        "none": "ok",
        "captures": "cap",
    }
    figs = charts.flight_charts(f, report_m2.config, labels, 65520)
    assert set(figs) == {"map", "mapll", "alt", "irr", "sat", "att", "int", "hist"}
    ll = figs["mapll"]
    assert ll["layout"]["meta"]["crs"] == "wgs84" and len(ll["layout"]["meta"]["affine"]) == 6
    assert figs["map"]["layout"]["meta"]["crs"] == "utm"
    cap_ll = next(d for d in ll["data"] if d["name"] == "cap")
    assert min(cap_ll["y"]) > 30 and max(cap_ll["x"]) < 180  # 위경도
    assert ll["layout"]["yaxis"]["scaleratio"] == 1  # EPSG:4326 정사각 격자
    assert figs["int"]["layout"]["showlegend"] is False  # 단일 트레이스: 범례 없음
    assert "showlegend" not in figs["alt"]["layout"]  # 범례는 템플릿 JS 가 HTML 로 그린다
    hs = {figs[k]["layout"]["height"] for k in ("alt", "irr", "sat", "att", "int", "hist")}
    assert len(hs) == 1  # 시계열 높이 통일
    for fig in figs.values():
        json.dumps(fig)  # 직렬화 가능
        assert fig["layout"]["paper_bgcolor"] == "rgba(0,0,0,0)"
    m = figs["map"]
    names = [d["name"] for d in m["data"]]
    assert "c" in names and "t" in names and "cap" in names
    cap = next(d for d in m["data"] if d["name"] == "cap")
    assert len(cap["x"]) == len(f.rows)
    assert tokens.LIGHT.fail in cap["marker"]["color"]  # 포화 캡처는 빨강
    counts = charts.status_counts(f)
    assert sum(counts.values()) == sum(len(g.metrics) for g in f.groups)


# --------------------------------------------------------------------------- QtWebEngine 실측


@pytest.mark.exiftool
@pytest.mark.qt
def test_render_png_pdf_and_layout(report_m2: RunReport, tmp_path: Path) -> None:
    from flightqa.report.pdf import html_to_pdf, html_to_png, measure_layout

    html = html_report.write_html(report_m2, tmp_path / "report.html")
    for width in (1280, 800, 400):
        m = measure_layout(html, width=width)
        assert m, "레이아웃 실측 실패"
        assert m["overflow"] == 0, f"{width}px 에서 가로 오버플로 {m['overflow']}개"
        assert m["sw"] <= m["cw"] + 1
        assert m["charts"] == 8
    pdf = tmp_path / "report.pdf"
    assert html_to_pdf(html, pdf)
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 100_000
    assert b"/FontFile" in data or b"/FontFile2" in data  # 폰트 임베드 (한글)
    assert re.search(rb"/Type\s*/Page[^s]", data)
    DOCS_SHOTS.mkdir(parents=True, exist_ok=True)
    assert html_to_png(html, DOCS_SHOTS / "report_synthetic_light.png", width=1280, height=1000)
    assert (DOCS_SHOTS / "report_synthetic_light.png").stat().st_size > 20_000
