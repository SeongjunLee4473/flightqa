"""자체 완결형 HTML 보고서 렌더 (CLAUDE.md §5.1).

- CSS(디자인 토큰)·plotly.js·차트 JSON·썸네일(PNG data URL) 을 모두 인라인. 인터넷 없이 열린다.
- 구조: 표지 → 종합 판정·권고 → 비행 요약 카드 → 항목별 상세(지도·시계열·히스토그램) → 제외·경고
  → 부록.
- 다국어: ``Translator`` (기본 ko). 근거 문장은 분석 모듈이 만든 한국어 그대로.
"""

from __future__ import annotations

import json
import platform
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import plotly
from jinja2 import Environment, PackageLoader, select_autoescape
from plotly.offline import get_plotlyjs

from flightqa import __version__
from flightqa.core.models import CheckGroup, FlightReport, RunReport, Status
from flightqa.core.textfmt import split_sentences
from flightqa.report import charts
from flightqa.report.i18n import Translator
from flightqa.report.thumbs import thumbnail_data_url
from flightqa.theme.tokens import BAND_COLORS, FONT_UI, LIGHT, LOGO_SVG, css_root

STATUS_CLASS = {Status.PASS: "pass", Status.WARN: "warn", Status.FAIL: "fail", Status.NA: "na"}


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("flightqa.report", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = fmt_num
    env.filters["dur"] = fmt_duration
    env.filters["json"] = lambda v: json.dumps(v, ensure_ascii=False)
    return env


def _group_ctx(g: CheckGroup, t: Translator) -> dict[str, Any]:
    """항목 그룹 → 템플릿용 dict (그룹별 표 하나; 근거·권고 조치은 문장 목록)."""
    return {
        "key": g.key,
        "label": t.group_label(g.key, g.label),
        "status": STATUS_CLASS[g.status],
        "status_text": t.status(g.status.value),
        "n": len(g.metrics),
        "n_fail": sum(1 for m in g.metrics if m.status is Status.FAIL),
        "n_warn": sum(1 for m in g.metrics if m.status is Status.WARN),
        "metrics": [
            {
                "key": m.key,
                "label": t.metric_label(m.key, m.label),
                "value": fmt_num(m.value, 2 if isinstance(m.value, float) else 1, m.unit or ""),
                "threshold": m.threshold or "",
                "status": STATUS_CLASS[m.status],
                "status_text": t.status(m.status.value),
                "reason": m.reason,
                "reason_lines": split_sentences(m.reason) if m.reason else [],
                "next_action": m.next_action or "",
                "action_lines": split_sentences(m.next_action) if m.next_action else [],
                "gate": m.gate,
            }
            for m in g.metrics
        ],
    }


def fmt_num(v: Any, digits: int = 1, unit: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        s = f"{v:,}"
    elif isinstance(v, float):
        s = f"{v:,.{digits}f}"
    else:
        s = str(v)
    return f"{s} {unit}".strip()


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    m, s = divmod(round(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_local(dt: datetime | None, offset_min: int | None) -> str:
    if dt is None:
        return "—"
    tz = timezone(timedelta(minutes=offset_min or 0))
    sign = "+" if (offset_min or 0) >= 0 else "-"
    om = abs(offset_min or 0)
    return f"{dt.astimezone(tz):%Y-%m-%d %H:%M:%S} (UTC{sign}{om // 60:02d}:{om % 60:02d})"


def git_hash() -> str | None:
    """개발 트리에서는 git, 얼린 배포본에서는 빌드 시 생성한 ``flightqa._build`` 에서 읽는다."""
    try:
        from flightqa import _build  # type: ignore[attr-defined,unused-ignore]

        return str(_build.GIT_HASH)
    except ImportError:
        pass
    try:
        root = Path(__file__).resolve().parents[3]
        if not (root / ".git").exists():
            return None
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _thumbs(f: FlightReport, t: Translator, sat_dn: int, enabled: bool) -> list[dict[str, str]]:
    if not enabled:
        return []
    picks: list[tuple[str, int]] = []
    if f.panel and f.panel.pre_indices:
        picks.append((t("thumb.panel"), f.panel.pre_indices[0]))
    sat = [(r.sat_max_pct or 0.0, r.index) for r in f.rows if r.sat_max_pct]
    if sat:
        picks.append((t("thumb.saturated"), max(sat)[1]))
    survey = {ln.index for ln in f.lines if not ln.is_transit}
    first = next((r.index for r in f.rows if r.line in survey and not r.is_ground), None)
    if first is not None:
        picks.append((t("thumb.first"), first))
    out: list[dict[str, str]] = []
    seen: set[int] = set()
    for label, idx in picks:
        if idx in seen:
            continue
        seen.add(idx)
        row = f.rows[idx]
        if not row.primary_file:
            continue
        url = thumbnail_data_url(Path(row.primary_file), max_width=260, sat_dn=sat_dn)
        if url:
            out.append(
                {
                    "label": label,
                    "src": url,
                    "caption": f"#{idx} · {Path(row.primary_file).name}"
                    + (f" · sat {row.sat_max_pct:.2f}%" if row.sat_max_pct else ""),
                }
            )
    return out


def build_context(
    report: RunReport, lang: str = "ko", *, thumbnails: bool = True
) -> dict[str, Any]:
    t = Translator(lang)
    cfg = report.config
    sensor_key = report.sensor.value if report.sensor else ""
    sat_dn = int(cfg.get("sensors", {}).get(sensor_key, {}).get("saturation_dn", 65520))
    labels = {
        "track": t("chart.legend.track"),
        "coverage": t("chart.legend.coverage"),
        "hole": t("chart.legend.hole"),
        "survey_area": t("chart.legend.survey_area"),
        "mission_area": t("chart.legend.mission_area"),
        "event": t("chart.legend.event"),
        "ground": t("chart.legend.ground"),
        "target": t("chart.legend.target"),
        "none": t("flag.none"),
        "captures": t("report.captures"),
        "lon": t("chart.axis.lon"),
        "lat": t("chart.axis.lat"),
        "agl_rel": t("chart.legend.agl_rel"),
    }
    flights: list[dict[str, Any]] = []
    for f in report.flights:
        s = f.summary
        ov = {m.key: m for g in f.groups for m in g.metrics}
        groups = [_group_ctx(g, t) for g in f.groups]
        fig = charts.flight_charts(f, cfg, labels, sat_dn)
        flights.append(
            {
                "id": s.flight_id,
                "index": s.index,
                "status": STATUS_CLASS[f.status],
                "status_text": t.status(f.status.value),
                "counts": charts.status_counts(f),
                "cards": [
                    (
                        t("card.captures"),
                        f"{s.n_captures:,}",
                        t("card.sub.complete", n=f"{s.n_complete:,}"),
                    ),
                    (t("card.duration"), fmt_duration(s.duration_s), t("card.sub.duration")),
                    (t("card.lines"), str(s.n_lines), t("card.sub.lines")),
                    (t("card.agl"), fmt_num(s.mean_agl_m, 1, "m"), f.agl_source),
                    (
                        t("card.interval"),
                        fmt_num(s.median_interval_s, 2, "s"),
                        t("card.sub.median"),
                    ),
                    (t("card.speed"), fmt_num(s.median_speed_mps, 2, "m/s"), t("card.sub.median")),
                    (
                        t("card.forward"),
                        fmt_num(ov["overlap.forward"].value, 1, "%")
                        if "overlap.forward" in ov
                        else "—",
                        t("card.sub.measured"),
                    ),
                    (
                        t("card.side"),
                        fmt_num(ov["overlap.side"].value, 1, "%") if "overlap.side" in ov else "—",
                        t("card.sub.measured"),
                    ),
                    (
                        t("card.area"),
                        fmt_num(ov["overlap.area"].value, 2, "ha") if "overlap.area" in ov else "—",
                        t("card.sub.area"),
                    ),
                    (
                        t("card.gsd"),
                        fmt_num(ov["overlap.gsd"].value, 2, "cm/px")
                        if "overlap.gsd" in ov
                        else "—",
                        t("card.sub.gsd"),
                    ),
                ],
                "start": fmt_local(s.start_utc, s.utc_offset_min),
                "end": fmt_local(s.end_utc, s.utc_offset_min),
                "groups": groups,
                "lines": [ln for ln in f.lines if not ln.is_transit],
                "n_transit": sum(1 for ln in f.lines if ln.is_transit),
                "charts": {
                    k: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                    for k, v in fig.items()
                },
                "thumbs": _thumbs(f, t, sat_dn, thumbnails),
                "agl_source": f.agl_source,
                "coverage": f.coverage,
            }
        )
    run_groups = [_group_ctx(g, t) for g in report.run_groups]
    multi_flight = len(report.flights) > 1
    first_offset = next((f.summary.utc_offset_min for f in report.flights), None)
    first_start = next((f.summary.start_utc for f in report.flights if f.summary.start_utc), None)
    excluded = [
        {"path": str(e.path), "reason": e.reason}
        for e in list(report.discovery.ignored) + list(report.excluded)
    ]
    return {
        "t": t,
        "lang": lang,
        "other_lang": "en" if lang == "ko" else "ko",
        "report": report,
        "status": STATUS_CLASS[report.status],
        "status_text": t.status(report.status.value),
        "headline": report.headline,
        "headline_detail": report.headline_detail,
        "next_actions": report.next_actions,
        "action_items": [
            {
                "status": STATUS_CLASS[it.status],
                "status_text": t.status(it.status.value),
                "metric_label": t.metric_label(it.metric_key, it.metric_label),
                "meta": " · ".join(
                    (
                        [f"{t('section.flight')} {it.flight_index + 1}"]
                        if multi_flight and it.flight_index is not None
                        else []
                    )
                    + [t.group_label(it.group_key, it.group_label)]
                ),
                "sentences": split_sentences(it.text),
            }
            for it in report.action_items
        ],
        "site": report.root.name,
        "root": str(report.root),
        "sensor": report.sensor_display or "—",
        "n_captures": report.n_captures,
        "n_flights": len(report.flights),
        "acq_date": fmt_local(first_start, first_offset)[:10] if first_start else "—",
        "generated": fmt_local(report.generated_at, first_offset),
        "flights": flights,
        "run_groups": run_groups,
        "excluded": excluded,
        "warnings": report.warnings,
        "config_json": json.dumps(cfg, ensure_ascii=False, indent=2),
        "sample_tags": report.sample_tags or {},
        "versions": {
            "flightqa": __version__,
            "git": git_hash() or "—",
            "exiftool": report.exiftool_version or "—",
            "python": platform.python_version(),
            "plotly": plotly.__version__,
            "os": platform.platform(),
        },
        "timings": report.timings_s,
        "css_root": css_root(),
        "logo_svg": LOGO_SVG,
        "favicon": _favicon(),
        "plotly_js": get_plotlyjs(),
        "band_colors": BAND_COLORS,
        "options_json": json.dumps(_options(cfg, t), ensure_ascii=False),
    }


def _favicon() -> str:
    """브라우저 탭 아이콘: 브랜드색 둥근 사각형 위의 로고 (data URI 라 오프라인에서도 보인다)."""
    from urllib.parse import quote

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        f'<rect width="32" height="32" rx="7" fill="{LIGHT.brand}"/>'
        '<g transform="translate(4 4)" fill="none" stroke="#fff" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round">{LOGO_SVG}</g></svg>'
    )
    return "data:image/svg+xml," + quote(svg)


BASEMAPS: dict[str, tuple[str, str]] = {
    "esri": (
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    ),
    "vworld": (
        "https://api.vworld.kr/req/wmts/1.0.0/{key}/Satellite/{z}/{y}/{x}.jpeg",
        "국토지리정보원 브이월드 (VWorld)",
    ),
}


def _options(cfg: dict[str, Any], t: Translator) -> dict[str, Any]:
    """템플릿 JS 에 넘기는 옵션: 글꼴, 인쇄용 글자색, 배경지도 (설정에서 허용한 경우만 URL 포함)."""
    rep = cfg.get("report", {})
    provider = str(rep.get("basemap_provider", "esri"))
    url, attribution = BASEMAPS.get(provider, ("", ""))
    if provider == "custom":
        url = str(rep.get("basemap_url", ""))
        attribution = str(rep.get("basemap_attribution", ""))
    key = str(rep.get("basemap_api_key", ""))
    if "{key}" in url and not key:
        url = ""  # 키 없는 제공자는 쓸 수 없음 → 버튼 숨김
    return {
        "font": FONT_UI,
        "lightText": LIGHT.text2,
        "basemap": {
            "url": url,
            "key": key,
            "attribution": attribution,
            "maxZoom": int(rep.get("basemap_max_zoom", 19)),
            "auto": bool(rep.get("online_basemap", False)),
        },
        "text": {
            "basemapLoading": t("chart.basemap_loading"),
            "basemapFail": t("chart.basemap_fail"),
        },
    }


def render_html(report: RunReport, lang: str = "ko", *, thumbnails: bool = True) -> str:
    tpl = _env().get_template("report.html.j2")
    return tpl.render(**build_context(report, lang, thumbnails=thumbnails))


def write_html(report: RunReport, path: Path, lang: str = "ko", *, thumbnails: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(report, lang, thumbnails=thumbnails), encoding="utf-8")
    return path
