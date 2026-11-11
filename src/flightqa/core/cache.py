"""증분 캐시 (SQLite).

키 = 경로+크기+mtime 해시 (``FileEntry.key``). 값 = slim 원본 태그 JSON (zlib).
같은 폴더 재분석 시 변경된 파일만 exiftool 을 거친다 (CLAUDE.md §7).
exiftool 버전이 다르면 미스로 취급한다 (태그 출력이 달라질 수 있음).
"""

from __future__ import annotations

import logging
import sqlite3
import time
import zlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from flightqa.core.metadata import FileEntry, json_to_raw, raw_to_json
from flightqa.core.sensors.base import Raw

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files (
    key TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    exiftool TEXT,
    raw BLOB NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS files_path ON files(path);
CREATE TABLE IF NOT EXISTS pixels (
    key TEXT PRIMARY KEY,
    v BLOB NOT NULL,
    created REAL NOT NULL
);
"""
CACHE_VERSION = "1"


class MetadataCache:
    def __init__(self, path: Path, exiftool_version: str | None) -> None:
        self.path = path
        self.exiftool_version = exiftool_version or ""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(path), check_same_thread=False)
        self._con.executescript(_SCHEMA)
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        ver = self._con.execute("SELECT v FROM meta WHERE k='version'").fetchone()
        if ver is None:
            self._con.execute("INSERT INTO meta(k, v) VALUES('version', ?)", (CACHE_VERSION,))
            self._con.commit()
        elif ver[0] != CACHE_VERSION:
            log.info("캐시 스키마 버전 불일치(%s → %s): 캐시를 비웁니다", ver[0], CACHE_VERSION)
            self.clear()
        self.hits = 0
        self.misses = 0

    def __enter__(self) -> MetadataCache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._con.commit()
        self._con.close()

    def clear(self) -> None:
        self._con.execute("DELETE FROM files")
        self._con.execute("DELETE FROM pixels")
        self._con.execute(
            "INSERT OR REPLACE INTO meta(k, v) VALUES('version', ?)", (CACHE_VERSION,)
        )
        self._con.commit()

    def get_many(self, entries: Sequence[FileEntry]) -> dict[str, Raw]:
        """키 → raw. 없거나 exiftool 버전이 다른 항목은 빠진다."""
        found: dict[str, Raw] = {}
        keys = [e.key for e in entries]
        for i in range(0, len(keys), 900):  # SQLite 변수 개수 제한
            batch = keys[i : i + 900]
            q = f"SELECT key, exiftool, raw FROM files WHERE key IN ({','.join('?' * len(batch))})"
            for key, ver, blob in self._con.execute(q, batch):
                if ver != self.exiftool_version:
                    continue
                found[key] = json_to_raw(zlib.decompress(blob))
        self.hits += len(found)
        self.misses += len(keys) - len(found)
        return found

    def put_many(self, items: Iterable[tuple[FileEntry, Raw]]) -> int:
        now = time.time()
        rows = [
            (
                e.key,
                str(e.path),
                e.size,
                e.mtime_ns,
                self.exiftool_version,
                zlib.compress(raw_to_json(raw), 6),
                now,
            )
            for e, raw in items
            if "ExifTool:Error" not in raw  # 실패는 캐시하지 않는다 (다음 실행에서 재시도)
        ]
        if rows:
            self._con.executemany(
                "INSERT OR REPLACE INTO files(key, path, size, mtime_ns, exiftool, raw, created)"
                " VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            self._con.commit()
        return len(rows)

    # ---- 픽셀 통계 (§4.5). 키 = 파일 키 + 포화 DN + stride + bins + bright 플래그

    def get_pixels(self, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), 900):
            batch = list(keys[i : i + 900])
            q = f"SELECT key, v FROM pixels WHERE key IN ({','.join('?' * len(batch))})"
            for key, blob in self._con.execute(q, batch):
                found[key] = json_to_raw(zlib.decompress(blob))
        return found

    def put_pixels(self, items: Iterable[tuple[str, dict[str, Any]]]) -> int:
        now = time.time()
        rows = [
            (key, zlib.compress(raw_to_json(v), 6), now) for key, v in items if "error" not in v
        ]
        if rows:
            self._con.executemany(
                "INSERT OR REPLACE INTO pixels(key, v, created) VALUES (?,?,?)", rows
            )
            self._con.commit()
        return len(rows)

    def count(self) -> int:
        row = self._con.execute("SELECT COUNT(*) FROM files").fetchone()
        return int(row[0]) if row else 0

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
