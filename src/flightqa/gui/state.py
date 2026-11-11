"""앱 상태 (QSettings): 창 위치, 최근 폴더, 테마, 언어, 프리셋, exiftool 경로 (CLAUDE.md §6.4)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QSettings

ORG = "FlightQA"
APP = "FlightQA"
MAX_RECENT = 12


@dataclass
class RecentEntry:
    path: str
    sensor: str = ""
    status: str = ""
    when: str = ""
    out_dir: str = ""
    n_captures: int = 0

    @property
    def when_dt(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.when)
        except ValueError:
            return None


class AppState:
    def __init__(self, settings: QSettings | None = None) -> None:
        self.s = settings or QSettings(ORG, APP)

    # ---- 창
    def geometry(self) -> QByteArray | None:
        v = self.s.value("window/geometry")
        return v if isinstance(v, QByteArray) else None

    def set_geometry(self, g: QByteArray) -> None:
        self.s.setValue("window/geometry", g)

    # ---- 테마·언어
    @property
    def theme(self) -> str:
        return str(self.s.value("ui/theme", "system"))

    @theme.setter
    def theme(self, v: str) -> None:
        self.s.setValue("ui/theme", v)

    @property
    def lang(self) -> str:
        return str(self.s.value("ui/lang", "ko"))

    @lang.setter
    def lang(self, v: str) -> None:
        self.s.setValue("ui/lang", v)

    # ---- 분석 옵션
    @property
    def preset_path(self) -> str:
        return str(self.s.value("analysis/preset", ""))

    @preset_path.setter
    def preset_path(self, v: str) -> None:
        self.s.setValue("analysis/preset", v)

    @property
    def exiftool_path(self) -> str:
        return str(self.s.value("analysis/exiftool", ""))

    @exiftool_path.setter
    def exiftool_path(self, v: str) -> None:
        self.s.setValue("analysis/exiftool", v)
        if v:
            os.environ["FLIGHTQA_EXIFTOOL"] = v
        else:
            os.environ.pop("FLIGHTQA_EXIFTOOL", None)

    @property
    def fast_mode(self) -> bool:
        return str(self.s.value("analysis/fast", "false")).lower() == "true"

    @fast_mode.setter
    def fast_mode(self, v: bool) -> None:
        self.s.setValue("analysis/fast", "true" if v else "false")

    @property
    def use_cache(self) -> bool:
        return str(self.s.value("analysis/cache", "true")).lower() == "true"

    @use_cache.setter
    def use_cache(self, v: bool) -> None:
        self.s.setValue("analysis/cache", "true" if v else "false")

    @property
    def out_mode(self) -> str:
        """'inside' = <폴더>/flightqa_out, 'custom' = out_root/<폴더명>."""
        return str(self.s.value("analysis/out_mode", "inside"))

    @out_mode.setter
    def out_mode(self, v: str) -> None:
        self.s.setValue("analysis/out_mode", v)

    @property
    def out_root(self) -> str:
        return str(self.s.value("analysis/out_root", ""))

    @out_root.setter
    def out_root(self, v: str) -> None:
        self.s.setValue("analysis/out_root", v)

    def out_dir_for(self, folder: Path) -> Path:
        if self.out_mode == "custom" and self.out_root:
            return Path(self.out_root) / folder.name
        from flightqa.core.discovery import OUTPUT_DIR_NAME

        return folder / OUTPUT_DIR_NAME

    # ---- 최근 목록
    def recent(self) -> list[RecentEntry]:
        raw = self.s.value("recent/list", "[]")
        try:
            data = json.loads(str(raw))
        except (TypeError, ValueError):
            return []
        out: list[RecentEntry] = []
        for d in data if isinstance(data, list) else []:
            try:
                out.append(
                    RecentEntry(**{k: d.get(k, "") for k in RecentEntry.__dataclass_fields__})
                )
            except TypeError:
                continue
        return out

    def add_recent(self, entry: RecentEntry) -> None:
        items = [
            r for r in self.recent() if os.path.normcase(r.path) != os.path.normcase(entry.path)
        ]
        entry.when = entry.when or datetime.now(UTC).isoformat()
        items.insert(0, entry)
        self._save_recent(items[:MAX_RECENT])

    def remove_recent(self, path: str) -> None:
        self._save_recent(
            [r for r in self.recent() if os.path.normcase(r.path) != os.path.normcase(path)]
        )

    def clear_recent(self) -> None:
        self._save_recent([])

    def _save_recent(self, items: list[RecentEntry]) -> None:
        self.s.setValue("recent/list", json.dumps([asdict(r) for r in items], ensure_ascii=False))

    def value(self, key: str, default: Any = None) -> Any:
        return self.s.value(key, default)

    def set_value(self, key: str, v: Any) -> None:
        self.s.setValue(key, v)

    def sync(self) -> None:
        self.s.sync()


def presets_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME") or str(Path.home())
    d = Path(base) / "flightqa" / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d
