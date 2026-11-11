"""디자인 토큰 (CLAUDE.md §6.2). 보고서 CSS 와 GUI QSS 가 같은 값을 쓴다.

색은 WCAG AA (텍스트 4.5:1) 를 흰/검 배경에서 확인한 값이다 (docs/DESIGN.md 에 대비 표).
Qt 나 브라우저에 의존하지 않는 순수 모듈이라 core/report 어디서나 import 할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class Palette:
    bg: str
    """페이지 바탕."""
    bg2: str
    """카드·표면."""
    bg3: str
    """구분 영역·헤더 행."""
    border: str
    text: str
    text2: str
    text3: str
    brand: str
    brand_soft: str
    on_brand: str
    pass_: str
    pass_soft: str
    warn: str
    warn_soft: str
    fail: str
    fail_soft: str
    na: str
    na_soft: str
    focus: str
    shadow1: str
    shadow2: str

    def css_vars(self) -> dict[str, str]:
        return {
            f"--{f.name.rstrip('_').replace('_', '-')}": getattr(self, f.name) for f in fields(self)
        }


LIGHT = Palette(
    bg="#F4F6F8",
    bg2="#FFFFFF",
    bg3="#EAEEF2",
    border="#D5DBE2",
    text="#171B21",
    text2="#4A5563",
    text3="#6A7381",
    brand="#0B6B67",
    brand_soft="#DDF1EF",
    on_brand="#FFFFFF",
    pass_="#1B7A3D",
    pass_soft="#DFF3E5",
    warn="#9A5A00",
    warn_soft="#FBEBD0",
    fail="#B3261E",
    fail_soft="#FADEDC",
    na="#5F6B7A",
    na_soft="#E7EAEE",
    focus="#2563EB",
    shadow1="0 1px 2px rgba(16, 24, 40, 0.06), 0 1px 3px rgba(16, 24, 40, 0.10)",
    shadow2="0 4px 12px rgba(16, 24, 40, 0.10), 0 2px 4px rgba(16, 24, 40, 0.06)",
)

DARK = Palette(
    bg="#0F1216",
    bg2="#171B21",
    bg3="#1F252C",
    border="#2C343E",
    text="#E8EAED",
    text2="#B6BDC6",
    text3="#8C95A1",
    brand="#4FC8C0",
    brand_soft="#12302F",
    on_brand="#0B1514",
    pass_="#5BD37F",
    pass_soft="#123322",
    warn="#F2B84B",
    warn_soft="#3A2A0A",
    fail="#F4837A",
    fail_soft="#3C1512",
    na="#9AA3AE",
    na_soft="#252B33",
    focus="#7AA7FF",
    shadow1="0 1px 2px rgba(0, 0, 0, 0.5)",
    shadow2="0 6px 16px rgba(0, 0, 0, 0.55)",
)

FONT_UI = (
    "'Segoe UI', 'Malgun Gothic', -apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', "
    "'Helvetica Neue', Arial, 'Noto Sans KR', sans-serif"
)
FONT_MONO = "Consolas, 'SF Mono', Menlo, 'D2Coding', monospace"

# 크기 스케일 5 단계 (px) — CLAUDE.md §6.2
SIZE_XS = 11
SIZE_SM = 12
SIZE_MD = 14
SIZE_LG = 18
SIZE_XL = 26

SPACE = 4  # 4px 그리드
RADIUS_SM = 6
RADIUS_LG = 12
MOTION_MS = 160

STATUS_COLOR_KEYS = {"PASS": "pass", "WARN": "warn", "FAIL": "fail", "N/A": "na"}


def css_root() -> str:
    """라이트 기본 + OS 다크 + 명시 토글(data-theme) 3 단계 CSS 변수 블록."""

    def block(p: Palette) -> str:
        return ";".join(f"{k}:{v}" for k, v in p.css_vars().items())

    return (
        f":root{{{block(LIGHT)};--font-ui:{FONT_UI};--font-mono:{FONT_MONO};"
        f"--fs-xs:{SIZE_XS}px;--fs-sm:{SIZE_SM}px;--fs-md:{SIZE_MD}px;--fs-lg:{SIZE_LG}px;"
        f"--fs-xl:{SIZE_XL}px;--sp:{SPACE}px;--r-sm:{RADIUS_SM}px;--r-lg:{RADIUS_LG}px;"
        f"--motion:{MOTION_MS}ms;color-scheme:light}}\n"
        f"@media (prefers-color-scheme: dark){{:root:not([data-theme=light]){{{block(DARK)};"
        f"color-scheme:dark}}}}\n"
        f":root[data-theme=dark]{{{block(DARK)};color-scheme:dark}}\n"
        # 인쇄·PDF 는 화면 테마와 무관하게 항상 라이트 (공유 문서의 일관성)
        f"@media print{{:root,:root[data-theme=dark],:root:not([data-theme=light])"
        f"{{{block(LIGHT)};color-scheme:light}}}}\n"
    )


def plotly_colors(dark: bool) -> dict[str, str]:
    p = DARK if dark else LIGHT
    return {
        "text": p.text2,
        "grid": p.border,
        "brand": p.brand,
        "pass": p.pass_,
        "warn": p.warn,
        "fail": p.fail,
        "na": p.na,
    }


BAND_COLORS = {
    "blue": "#2F6FE4",
    "green": "#2E9E5B",
    "red": "#D93A3A",
    "rededge": "#B5473C",
    "nir": "#7A3FB0",
    "rgb": "#6B7280",
}

LOGO_SVG = (
    '<circle cx="5.5" cy="5.5" r="3"/><circle cx="18.5" cy="5.5" r="3"/>'
    '<circle cx="5.5" cy="18.5" r="3"/><circle cx="18.5" cy="18.5" r="3"/>'
    '<path d="M7.7 7.7l2.1 2.1M16.3 7.7l-2.1 2.1M7.7 16.3l2.1-2.1M16.3 16.3l-2.1-2.1"/>'
    '<rect x="9.6" y="9.6" width="4.8" height="4.8" rx="1.4"/>'
)
"""앱 로고 (쿼드콥터 평면도, 24×24 선 아이콘의 SVG 본문). GUI 아이콘 세트·앱 아이콘·보고서 상단·
파비콘이 모두 이 한 정의를 쓴다 — 로고를 바꿀 때 한 곳만 고치면 된다."""
