"""문장 단위 줄바꿈 등 표시용 텍스트 도우미. GUI·보고서가 같은 규칙을 쓴다."""

from __future__ import annotations

import re

# 마침표·물음표·느낌표 뒤에 공백이 오면 문장 경계. "1.5 s", "PPKNAV.nav," 처럼 뒤에 공백이 없거나
# 쉼표가 붙은 약어는 자르지 않는다.
_SENT = re.compile(r"\n+|(?<=[.!?])[ \t]+(?=\S)")


def split_sentences(text: str) -> list[str]:
    """권고 조치·근거 문장을 줄바꿈과 마침표 직후에서 나눈다 (빈 조각 제거).

    권고 조치는 "조치 줄\\n영향: … / 원인: …" 꼴의 개조식이라 줄바꿈이 1차 경계다.
    """
    return [s.strip() for s in _SENT.split(text.strip()) if s.strip()]
