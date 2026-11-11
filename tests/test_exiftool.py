from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from flightqa.core import exiftool as et


def test_find_explicit_path_ok(tmp_path: Path) -> None:
    fake = tmp_path / "exiftool.exe"
    fake.write_bytes(b"")
    assert et.find_exiftool(fake) == fake


def test_find_explicit_path_missing(tmp_path: Path) -> None:
    with pytest.raises(et.ExifToolNotFoundError):
        et.find_exiftool(tmp_path / "nope.exe")


def test_find_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "exiftool.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("FLIGHTQA_EXIFTOOL", str(fake))
    assert et.find_exiftool() == fake


def test_find_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLIGHTQA_EXIFTOOL", raising=False)
    monkeypatch.setattr(et, "bundled_candidates", list)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(et.ExifToolNotFoundError):
        et.find_exiftool()


def test_bundled_candidates_prefers_meipass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    cands = et.bundled_candidates()
    assert cands[0] == tmp_path / "tools" / "exiftool" / et.EXE_NAME
    assert len(cands) == 2


def test_read_tags_empty() -> None:
    assert et.read_tags([]) == []


@pytest.mark.exiftool
def test_version(exiftool_exe: Path) -> None:
    v = et.exiftool_version(exiftool_exe)
    assert v.count(".") == 1
    assert float(v) >= 12.0


@pytest.mark.exiftool
def test_read_tags_synth(exiftool_exe: Path, synth_tiff: Path) -> None:
    (rec,) = et.read_tags([synth_tiff], exiftool_exe)
    assert rec["IFD0:ImageWidth"] == 64
    assert rec["IFD0:ImageHeight"] == 48
    assert rec["IFD0:BitsPerSample"] == 16
    assert rec["File:FileType"] == "TIFF"


@pytest.mark.exiftool
def test_read_tags_non_ascii_path(exiftool_exe: Path, tmp_path: Path) -> None:
    """한글 폴더/파일명 (M3M 미션 폴더명이 한글인 경우가 실제로 있다)."""
    from fixtures.synth import make_tiff

    p = make_tiff(tmp_path / "영역-경로-만들기8" / "DJI_0001_MS_G.TIF", width=16, height=8)
    (rec,) = et.read_tags([p], exiftool_exe, numeric=True)
    assert rec["IFD0:ImageWidth"] == 16
    assert Path(rec["SourceFile"]).name == "DJI_0001_MS_G.TIF"


@pytest.mark.exiftool
def test_read_tags_partial_failure_keeps_good_files(exiftool_exe: Path, synth_tiff: Path) -> None:
    """깨진 파일이 섞여도 정상 파일의 결과는 돌려준다 (예외로 전체를 멈추지 않는다)."""
    bad = synth_tiff.parent / "broken.tif"
    bad.write_bytes(os.urandom(64))
    recs = et.read_tags([synth_tiff, bad], exiftool_exe)
    names = {Path(r["SourceFile"]).name for r in recs}
    assert synth_tiff.name in names
    broken = [r for r in recs if Path(r["SourceFile"]).name == "broken.tif"]
    assert broken and "ExifTool:Error" in broken[0]
