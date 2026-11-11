from __future__ import annotations

from pathlib import Path

from flightqa.core.cache import CACHE_VERSION, MetadataCache
from flightqa.core.metadata import FileEntry


def _entry(tmp_path: Path, name: str, content: bytes = b"II*\x00abcd") -> FileEntry:
    p = tmp_path / name
    p.write_bytes(content)
    return FileEntry.from_path(p)


def test_roundtrip_and_invalidation(tmp_path: Path) -> None:
    e1 = _entry(tmp_path, "a.tif")
    e2 = _entry(tmp_path, "b.tif")
    bad = _entry(tmp_path, "c.tif")
    with MetadataCache(tmp_path / "c" / "cache.sqlite", "13.59") as cache:
        assert cache.get_many([e1, e2]) == {}
        assert cache.misses == 2
        n = cache.put_many(
            [
                (e1, {"IFD0:Make": "MicaSense", "XMP-Camera:BandName": "Blue"}),
                (e2, {"IFD0:Make": "DJI"}),
                (bad, {"ExifTool:Error": "boom"}),  # 실패는 캐시하지 않는다
            ]
        )
        assert n == 2
        got = cache.get_many([e1, e2, bad])
        assert got[e1.key]["XMP-Camera:BandName"] == "Blue"
        assert e2.key in got and bad.key not in got
        assert cache.count() == 2 and cache.size_bytes() > 0
    # 파일이 바뀌면 (크기/mtime) 키가 달라져 미스
    e1b = _entry(tmp_path, "a.tif", b"II*\x00abcdef")
    assert e1b.key != e1.key
    with MetadataCache(tmp_path / "c" / "cache.sqlite", "13.59") as cache:
        assert e1b.key not in cache.get_many([e1b])
        assert e1.key in cache.get_many([e1])
    # exiftool 버전이 다르면 미스
    with MetadataCache(tmp_path / "c" / "cache.sqlite", "13.60") as cache:
        assert cache.get_many([e1]) == {}
        cache.clear()
        assert cache.count() == 0


def test_schema_version_mismatch_clears(tmp_path: Path) -> None:
    p = tmp_path / "cache.sqlite"
    e1 = _entry(tmp_path, "a.tif")
    with MetadataCache(p, "13.59") as cache:
        cache.put_many([(e1, {"x": 1})])
        cache._con.execute("UPDATE meta SET v='0' WHERE k='version'")
        cache._con.commit()
    with MetadataCache(p, "13.59") as cache:
        assert cache.count() == 0
        row = cache._con.execute("SELECT v FROM meta WHERE k='version'").fetchone()
        assert row[0] == CACHE_VERSION


def test_many_keys_batching(tmp_path: Path) -> None:
    entries = [FileEntry(path=tmp_path / f"{i}.tif", size=i, mtime_ns=i) for i in range(2000)]
    with MetadataCache(tmp_path / "cache.sqlite", "v") as cache:
        cache.put_many([(e, {"i": e.size}) for e in entries])
        got = cache.get_many(entries)
        assert len(got) == 2000 and got[entries[1500].key] == {"i": 1500}
        assert cache.hits == 2000
