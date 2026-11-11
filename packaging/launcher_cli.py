"""PyInstaller CLI 진입점 (flightqa-cli.exe)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from flightqa.cli import app

    sys.exit(app())
