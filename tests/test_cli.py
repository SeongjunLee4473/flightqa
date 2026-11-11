from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from flightqa import __version__
from flightqa.cli import app

runner = CliRunner()


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output or "사용법" in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.exiftool
def test_tags_command(exiftool_exe: Path, synth_tiff: Path) -> None:
    result = runner.invoke(app, ["tags", str(synth_tiff)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["IFD0:ImageWidth"] == 64


def test_tags_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(app, ["tags", str(tmp_path / "nope.tif")])
    assert result.exit_code != 0
