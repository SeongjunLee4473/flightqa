"""보고서·UI 문자열 리소스 (CLAUDE.md §5.4). 기본 한국어, 영어 전환.

문자열은 ``i18n/<lang>.json`` 에 있다. 분석 모듈이 만드는 근거(reason)·권고 조치 문장은 한국어로
생성되며, 영어 보고서에서는 항목 라벨·섹션·표 머리글만 번역되고 근거 문장은 한국어 그대로 남는다
(docs/ASSUMPTIONS.md M3). 키가 없으면 키 이름을 그대로 돌려줘 조용히 빈 문자열이 되지 않게 한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

DEFAULT_LANG = "ko"
LANGS = ("ko", "en")


@lru_cache(maxsize=4)
def _load(lang: str) -> dict[str, Any]:
    if lang not in LANGS:
        lang = DEFAULT_LANG
    text = (
        resources.files("flightqa.report.i18n_data")
        .joinpath(f"{lang}.json")
        .read_text(encoding="utf-8")
    )
    data: dict[str, Any] = json.loads(text)
    return data


class Translator:
    def __init__(self, lang: str = DEFAULT_LANG) -> None:
        self.lang = lang if lang in LANGS else DEFAULT_LANG
        self._d = _load(self.lang)
        self._fallback = _load(DEFAULT_LANG)

    def __call__(self, key: str, **kw: Any) -> str:
        s = self._d.get(key)
        if s is None:
            s = self._fallback.get(key, key)
        try:
            return str(s).format(**kw) if kw else str(s)
        except (KeyError, IndexError):
            return str(s)

    def metric_label(self, key: str, default: str) -> str:
        labels: dict[str, str] = self._d.get("metric_labels", {})
        return labels.get(key, default if self.lang == DEFAULT_LANG else labels.get(key, default))

    def group_label(self, key: str, default: str) -> str:
        labels: dict[str, str] = self._d.get("group_labels", {})
        return labels.get(key, default)

    def status(self, value: str) -> str:
        labels: dict[str, str] = self._d.get("status", {})
        return labels.get(value, value)
