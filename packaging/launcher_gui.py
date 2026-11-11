"""PyInstaller GUI 진입점 (FlightQA.exe)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from flightqa.gui.app import main

    sys.exit(main())
