"""FlightQA: UAV 다중분광 비행 데이터 품질 진단 도구."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("flightqa")
except PackageNotFoundError:  # 소스 트리에서 설치 없이 import 한 경우
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
