"""DJI 임무 파일(KMZ / WPML) 읽기.

근거: docs/tags_dji_wpml.md (DJI Pilot 2 → RC Pro 로 내보낸 실제 KMZ, WPML 1.0.6, 2026-09-30).
- KMZ 는 zip 이고 ``wpmz/template.kml`` (임무 폴리곤·파라미터) + ``wpmz/waylines.wpml``
  (웨이포인트).
- 매핑 임무는 ``<wpml:templateType>mapping2d</wpml:templateType>`` 이고 ``Placemark/Polygon`` 이
  조정기에서 그린 구역이다. ``wpml:margin`` 은 폴리곤 바깥으로 라인을 늘리는 여유(m).
- 좌표는 ``lon,lat[,alt]`` (WGS84). ``takeOffRefPoint`` 만 ``lat,lon,alt`` 순서다.
- 임무 폴리곤은 영상 EXIF/XMP 에 없으므로 이 파일이 있을 때만 "임무 구역" 이 되고, 없으면
  궤적 추정이다.

이 모듈은 좌표를 로그에 남기지 않는다 (CLAUDE.md §8).
"""

from __future__ import annotations

import logging
import os
import re
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pyproj import Transformer
from shapely import prepare
from shapely.geometry import Point, shape
from shapely.ops import transform as shp_transform

from flightqa.core.models import MissionPlan

log = logging.getLogger(__name__)

MISSION_EXTENSIONS = {".kmz", ".kml"}
_NS = {"kml": "http://www.opengis.net/kml/2.2"}
_WPML_NS_RE = re.compile(r"xmlns:wpml=\"(http://www\.dji\.com/wpmz/([\d.]+))\"")
_SKIP_DIRS = {"flightqa_out", "__MACOSX", ".git"}
_DJI_FOLDER_RE = re.compile(r"^DJI_\d{12}_\d{3}_(.+)$")


class MissionError(ValueError):
    """임무 파일을 읽을 수 없거나 매핑 폴리곤이 없다."""


# --------------------------------------------------------------------------- 찾기


def find_mission_files(root: Path, *, max_depth: int = 3) -> list[Path]:
    """데이터 폴더 아래의 KMZ/KML (깊이 제한, 출력 폴더 제외). 정렬된 경로 목록."""
    out: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        if rel_depth >= max_depth:
            dirnames[:] = []
        for name in sorted(filenames):
            if Path(name).suffix.lower() in MISSION_EXTENSIONS:
                out.append(Path(dirpath) / name)
    return out


def mission_name_of_folder(folder: Path) -> str | None:
    """DJI 미션 폴더 ``DJI_YYYYMMDDHHMM_NNN_<임무명>`` 의 임무명."""
    m = _DJI_FOLDER_RE.match(folder.name)
    return m.group(1) if m else None


def choose_mission_file(files: list[Path], root: Path) -> tuple[Path | None, str | None]:
    """후보 중 하나를 고른다. (선택, 경고). 폴더명의 임무명과 파일 이름이 같으면 그것을 우선."""
    if not files:
        return None, None
    names = {mission_name_of_folder(root)} | {
        n for p in root.rglob("DJI_*") if p.is_dir() and (n := mission_name_of_folder(p))
    }
    names.discard(None)
    for f in files:
        if f.stem in names:
            return f, None
    if len(files) == 1:
        return files[0], None
    return files[0], (
        f"임무 파일이 {len(files)}개 있어 첫 파일을 씀: {files[0].name} "
        "(설정 survey_area.mission_path 로 지정 가능)"
    )


def match_mission(
    plans: Sequence[MissionPlan],
    points: Sequence[tuple[float, float]],
    root: Path,
    start_utc: datetime | None,
    min_pct: float,
) -> tuple[MissionPlan | None, float]:
    """비행 하나에 맞는 임무 파일을 고른다. (선택, 폴리곤 안 캡처 비율 %).

    1. 조사 라인 캡처점(lon, lat) 중 폴리곤 안에 드는 비율이 ``min_pct`` 이상인 파일 가운데
       최대.
    2. 비율이 비슷하면(5 %p 이내) 폴더명의 임무명과 같은 파일 → 생성 시각이 비행 시작에 가장
       가까운 파일.
    3. 좌표가 없으면 이름 일치 파일, 그것도 없고 파일이 하나뿐이면 그 파일.
    """
    if not plans:
        return None, 0.0
    names = {mission_name_of_folder(root)} | {
        n for p in root.rglob("DJI_*") if p.is_dir() and (n := mission_name_of_folder(p))
    }
    names.discard(None)

    def name_hit(pl: MissionPlan) -> bool:
        return Path(pl.source).stem in names

    if not points:
        for pl in plans:
            if name_hit(pl):
                return pl, 0.0
        return (plans[0], 0.0) if len(plans) == 1 else (None, 0.0)
    scored: list[tuple[float, MissionPlan]] = []
    for pl in plans:
        poly = shape(pl.polygon_geojson)
        prepare(poly)
        inside = sum(1 for lon, lat in points if poly.covers(Point(lon, lat)))
        scored.append((inside / len(points) * 100.0, pl))
    best = max(f for f, _ in scored)
    if best < min_pct:
        return None, best
    tied = [(f, pl) for f, pl in scored if f >= best - 5.0]
    if len(tied) > 1:
        named = [t for t in tied if name_hit(t[1])]
        if named:
            tied = named
    if len(tied) > 1 and start_utc is not None:
        tied.sort(
            key=lambda t: (
                abs((start_utc - t[1].created_utc).total_seconds())
                if t[1].created_utc
                else float("inf")
            )
        )
    tied.sort(key=lambda t: -t[0]) if len(tied) > 1 and start_utc is None else None
    return tied[0][1], round(tied[0][0], 1)


def polygon_area_m2(plan: MissionPlan) -> float | None:
    """임무 폴리곤 면적 (m², UTM 투영). 좌표가 없으면 None."""
    geom = shape(plan.polygon_geojson)
    if geom.is_empty:
        return None
    c = geom.centroid
    zone = int((c.x + 180) // 6) + 1
    epsg = (32600 if c.y >= 0 else 32700) + zone
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    return float(shp_transform(tr.transform, geom).area)


# --------------------------------------------------------------------------- 읽기


def _read_documents(path: Path) -> tuple[str, str | None]:
    """(template.kml 텍스트, waylines.wpml 텍스트 | None)."""
    if not path.is_file():
        raise MissionError(f"임무 파일이 없음: {path.name}")
    if path.suffix.lower() == ".kml":
        return path.read_text(encoding="utf-8"), None
    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            tmpl = next((names[n] for n in names if n.endswith("template.kml")), None)
            if tmpl is None:
                tmpl = next((names[n] for n in names if n.endswith(".kml")), None)
            if tmpl is None:
                raise MissionError(f"KMZ 안에 template.kml 이 없음: {path.name}")
            wp = next((names[n] for n in names if n.endswith("waylines.wpml")), None)
            return (
                zf.read(tmpl).decode("utf-8"),
                zf.read(wp).decode("utf-8") if wp else None,
            )
    except zipfile.BadZipFile as e:
        raise MissionError(f"KMZ(zip) 가 아님: {path.name}") from e


def _coords(text: str | None) -> list[tuple[float, float]]:
    """``lon,lat[,alt]`` 토큰 목록 → [(lon, lat)]."""
    out: list[tuple[float, float]] = []
    for tok in (text or "").split():
        parts = tok.split(",")
        if len(parts) < 2:
            continue
        try:
            out.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    return out


def _wpml(doc: ET.Element, ns: dict[str, str], tag: str) -> str | None:
    el = doc.find(f".//wpml:{tag}", ns)
    return el.text.strip() if el is not None and el.text else None


def _f(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _i(v: str | None) -> int | None:
    f = _f(v)
    return int(f) if f is not None else None


def load_mission(path: Path) -> MissionPlan:
    """KMZ/KML → :class:`MissionPlan`. 매핑 폴리곤이 없으면 :class:`MissionError`."""
    tmpl_text, wp_text = _read_documents(path)
    m = _WPML_NS_RE.search(tmpl_text)
    ns = dict(_NS)
    if m:
        ns["wpml"] = m.group(1)
        version: str | None = m.group(2)
    else:
        ns["wpml"] = "http://www.dji.com/wpmz/1.0.6"
        version = None
    try:
        root = ET.fromstring(tmpl_text)
    except ET.ParseError as e:
        raise MissionError(f"KML 파싱 실패 {path.name}: {e}") from e

    polygons: list[list[tuple[float, float]]] = []
    for ring in root.findall(
        ".//kml:Placemark//kml:Polygon//kml:outerBoundaryIs//kml:coordinates", ns
    ):
        pts = _coords(ring.text)
        if len(pts) >= 3:
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            polygons.append(pts)
    if not polygons:
        raise MissionError(
            f"매핑 폴리곤(Placemark/Polygon) 이 없음: {path.name} "
            f"(templateType={_wpml(root, ns, 'templateType') or '?'})"
        )
    geometry: dict[str, Any] = (
        {"type": "Polygon", "coordinates": [[list(p) for p in polygons[0]]]}
        if len(polygons) == 1
        else {"type": "MultiPolygon", "coordinates": [[[list(p) for p in pg]] for pg in polygons]}
    )

    waypoints: list[tuple[float, float]] = []
    distance = duration = None
    if wp_text:
        try:
            wroot = ET.fromstring(wp_text)
            for pt in wroot.findall(".//kml:Placemark/kml:Point/kml:coordinates", ns):
                waypoints.extend(_coords(pt.text)[:1])
            distance = _f(_wpml(wroot, ns, "distance"))
            duration = _f(_wpml(wroot, ns, "duration"))
        except ET.ParseError as e:
            log.warning("waylines.wpml 파싱 실패 (%s): 웨이포인트 생략", e)

    create = _i(_wpml(root, ns, "createTime"))
    takeoff = _coords_latlon(_wpml(root, ns, "takeOffRefPoint"))
    overlap_h = _i(_wpml(root, ns, "orthoCameraOverlapH"))
    overlap_w = _i(_wpml(root, ns, "orthoCameraOverlapW"))
    return MissionPlan(
        source=path.name,
        wpml_version=version,
        template_type=_wpml(root, ns, "templateType"),
        created_utc=datetime.fromtimestamp(create / 1000.0, tz=UTC) if create else None,
        drone_enum=_i(_wpml(root, ns, "droneEnumValue")),
        payload_enum=_i(_wpml(root, ns, "payloadEnumValue")),
        height_mode=_wpml(root, ns, "heightMode"),
        height_m=_f(_wpml(root, ns, "height")),
        shoot_height_m=_f(_wpml(root, ns, "globalShootHeight")),
        margin_m=_f(_wpml(root, ns, "margin")),
        overlap_h_pct=overlap_h,
        overlap_w_pct=overlap_w,
        direction_deg=_f(_wpml(root, ns, "direction")),
        speed_mps=_f(_wpml(root, ns, "autoFlightSpeed")),
        shoot_type=_wpml(root, ns, "shootType"),
        elevation_optimize=_wpml(root, ns, "elevationOptimizeEnable") == "1",
        image_format=_wpml(root, ns, "imageFormat"),
        takeoff_ref=takeoff,
        polygon_geojson=geometry,
        n_polygon_vertices=sum(len(p) - 1 for p in polygons),
        waypoints_geojson=(
            {"type": "LineString", "coordinates": [list(p) for p in waypoints]}
            if len(waypoints) >= 2
            else None
        ),
        n_waypoints=len(waypoints),
        distance_m=distance,
        duration_s=duration,
    )


def _coords_latlon(text: str | None) -> list[float] | None:
    """``takeOffRefPoint`` 는 ``lat,lon,alt`` 순서 → [lat, lon, alt]."""
    if not text:
        return None
    try:
        vals = [float(v) for v in text.split(",")]
    except ValueError:
        return None
    return vals if len(vals) >= 2 else None
