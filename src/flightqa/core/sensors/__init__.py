"""센서 플러그인 레지스트리 (CLAUDE.md §3.2). 새 센서는 여기에 한 줄 추가."""

from __future__ import annotations

from flightqa.core.models import CameraModel, SensorKind
from flightqa.core.sensors.base import Raw, SensorPlugin
from flightqa.core.sensors.dji_m3m import DjiM3M
from flightqa.core.sensors.rededge_mx import RedEdgeMX

__all__ = ["Raw", "SensorPlugin", "all_sensors", "get_sensor"]


def all_sensors(cameras: dict[SensorKind, CameraModel] | None = None) -> list[SensorPlugin]:
    cams = cameras or {}
    return [
        RedEdgeMX(cams.get(SensorKind.REDEDGE_MX)),
        DjiM3M(cams.get(SensorKind.DJI_M3M)),
    ]


def get_sensor(kind: SensorKind, camera: CameraModel | None = None) -> SensorPlugin:
    for s in all_sensors({kind: camera} if camera else None):
        if s.kind is kind:
            return s
    raise KeyError(kind)
