"""QGIS 용 GIS 내보내기 — GDAL/rasterio 없이 (CLAUDE.md §3.4 오프라인 exe 배포 부담 없음).

- ``flightqa.gpkg``: GeoPackage 1.3 (sqlite3 + WKB). 레이어 captures·track·coverage·holes·
  survey_area·uncovered, 전부 EPSG:4326. QGIS 에 드래그하면 레이어별로 열린다.
- ``flightqa.kml``: 같은 내용의 KML (Google Earth, 조정기 재투입 참고용).
- ``survey_area.geojson``: 추정 조사 구역과 미촬영 조각.
- ``overlap_count.tif``: 셀마다 그 자리를 덮는 영상 수 (uint16, UTM GeoTIFF).
- ``survey_area.tif``: 0 구역 밖 · 1 구역 안 미촬영 · 2 구역 안 촬영 (uint8).

GeoPackage 바이너리 헤더·테이블 정의는 OGC 12-128r18 (GeoPackage 1.3.1) 기준이고, GeoTIFF 는
``analysis/dem.py`` 가 읽는 것과 같은 태그(ModelPixelScale·ModelTiepoint·GeoKeyDirectory) 를 쓴다.
"""

from __future__ import annotations

import math
import sqlite3
import struct
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import shapely
import tifffile
from pyproj import CRS, Transformer
from pyproj.enums import WktVersion
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform

from flightqa.config.schema import Config
from flightqa.core.analysis.overlap import footprints
from flightqa.core.models import CaptureRow, FlightReport, MissionPlan, RunReport

GPKG_APPLICATION_ID = 0x47504B47
GPKG_USER_VERSION = 10301
SRS_ID = 4326

# --------------------------------------------------------------------------- 공통


def _multi(geom: BaseGeometry) -> MultiPolygon | None:
    if geom.is_empty:
        return None
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    polys = [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]
    return MultiPolygon(polys) if polys else None


def _flight_layers(f: FlightReport) -> dict[str, list[tuple[BaseGeometry, dict[str, Any]]]]:
    """비행 하나의 레이어별 (지오메트리, 속성) 목록. GeoPackage·KML 이 공유한다."""
    fid = f.summary.flight_id
    out: dict[str, list[tuple[BaseGeometry, dict[str, Any]]]] = {
        "captures": [],
        "track": [],
        "coverage": [],
        "holes": [],
        "survey_area": [],
        "uncovered": [],
    }
    coords = [(r.lon, r.lat) for r in f.rows if r.lon is not None and r.lat is not None]
    if len(coords) >= 2:
        out["track"].append(
            (
                LineString(coords),
                {"flight_id": fid, "status": f.status.value, "n_captures": f.summary.n_captures},
            )
        )
    for r in f.rows:
        if r.lon is None or r.lat is None:
            continue
        out["captures"].append((Point(r.lon, r.lat), _capture_attrs(f, r)))
    cov = f.coverage
    if cov is not None and cov.geojson:
        m = _multi(shape(cov.geojson))
        if m is not None:
            out["coverage"].append(
                (m, {"flight_id": fid, "area_m2": cov.area_m2, "n_holes": cov.n_holes})
            )
        for c in cov.hole_centroids:
            out["holes"].append((Point(c[0], c[1]), {"flight_id": fid}))
    sa = f.survey_area
    if sa is not None and sa.geojson:
        m = _multi(shape(sa.geojson))
        if m is not None:
            out["survey_area"].append(
                (
                    m,
                    {
                        "flight_id": fid,
                        "method": sa.method,
                        "source": sa.source,
                        "area_m2": sa.area_m2,
                        "fill_pct": sa.fill_pct,
                        "uncovered_area_m2": sa.uncovered_area_m2,
                        "margin_m": sa.margin_m,
                        "hull_ratio": sa.hull_ratio,
                        "n_points": sa.n_points,
                    },
                )
            )
        if sa.uncovered_geojson:
            m = _multi(shape(sa.uncovered_geojson))
            if m is not None:
                out["uncovered"].append((m, {"flight_id": fid, "area_m2": sa.uncovered_area_m2}))
    return out


def _capture_attrs(f: FlightReport, r: CaptureRow) -> dict[str, Any]:
    return {
        "flight_id": f.summary.flight_id,
        "flight_index": f.summary.index,
        "idx": r.index,
        "capture_id": r.capture_id,
        "time_utc": r.time_utc.isoformat() if r.time_utc else None,
        "agl_m": r.agl_m,
        "alt_msl_m": r.alt_msl_m,
        "speed_mps": r.speed_mps,
        "heading_deg": r.heading_deg,
        "line": r.line,
        "off_nadir_deg": r.off_nadir_deg,
        "forward_overlap_pct": r.forward_overlap_pct,
        "gsd_cm": r.gsd_cm,
        "sat_max_pct": r.sat_max_pct,
        "rtk_fixed": r.rtk_fixed,
        "is_ground": r.is_ground,
        "flags": r.flags,
        "primary_file": r.primary_file,
    }


LAYER_GEOM = {
    "captures": "POINT",
    "track": "LINESTRING",
    "coverage": "MULTIPOLYGON",
    "holes": "POINT",
    "survey_area": "MULTIPOLYGON",
    "uncovered": "MULTIPOLYGON",
    "mission_polygon": "MULTIPOLYGON",
    "mission_waypoints": "LINESTRING",
}
LAYER_TITLE = {
    "captures": "촬영 지점 (capture points)",
    "track": "비행 궤적 (track)",
    "coverage": "실측 커버리지 합집합 (coverage)",
    "holes": "커버리지 공백 중심 (holes)",
    "survey_area": "조사 구역 — 임무 KMZ 또는 궤적 추정 (survey area, see method)",
    "uncovered": "조사 구역 내 미촬영 (uncovered)",
    "mission_polygon": "조정기 임무 폴리곤 (mission polygon, KMZ)",
    "mission_waypoints": "조정기 임무 웨이포인트 순서 (mission waypoints, KMZ)",
}
REPORT_LAYERS = ("mission_polygon", "mission_waypoints")
"""비행이 아니라 데이터셋(임무 파일) 단위 레이어."""


def _mission_layers(
    plans: Sequence[MissionPlan],
) -> dict[str, list[tuple[BaseGeometry, dict[str, Any]]]]:
    out: dict[str, list[tuple[BaseGeometry, dict[str, Any]]]] = {k: [] for k in REPORT_LAYERS}
    for mp in plans:
        attrs = {
            "source": mp.source,
            "template_type": mp.template_type,
            "created_utc": mp.created_utc.isoformat() if mp.created_utc else None,
            "height_m": mp.height_m,
            "height_mode": mp.height_mode,
            "margin_m": mp.margin_m,
            "overlap_h_pct": mp.overlap_h_pct,
            "overlap_w_pct": mp.overlap_w_pct,
            "direction_deg": mp.direction_deg,
            "speed_mps": mp.speed_mps,
            "elevation_optimize": mp.elevation_optimize,
            "drone_enum": mp.drone_enum,
            "payload_enum": mp.payload_enum,
        }
        m = _multi(shape(mp.polygon_geojson))
        if m is not None:
            out["mission_polygon"].append((m, attrs))
        if mp.waypoints_geojson:
            out["mission_waypoints"].append(
                (
                    shape(mp.waypoints_geojson),
                    {
                        "source": mp.source,
                        "n_waypoints": mp.n_waypoints,
                        "distance_m": mp.distance_m,
                        "duration_s": mp.duration_s,
                    },
                )
            )
    return out


def _report_layers(report: RunReport) -> dict[str, list[tuple[BaseGeometry, dict[str, Any]]]]:
    plans = report.missions or ([report.mission] if report.mission else [])
    return _mission_layers(plans)


def write_missions_gis(plans: Sequence[MissionPlan], out_dir: Path) -> list[Path]:
    """임무 파일들을 QGIS 용으로: 임무마다 ``<이름>.gpkg`` + ``<이름>.geojson``, 2개 이상이면 합본
    ``missions.gpkg`` 도 만든다 (분석과 무관한 독립 도구)."""
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for mp in plans:
        stem = Path(mp.source).stem
        layers = _mission_layers([mp])
        written.append(write_gpkg_layers(layers, out_dir / f"{stem}.gpkg"))
        feats = [
            {
                "type": "Feature",
                "geometry": shapely.geometry.mapping(g),
                "properties": {"kind": k, **a},
            }
            for k, fs in layers.items()
            for g, a in fs
        ]
        gj = out_dir / f"{stem}.geojson"
        gj.write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False),
            encoding="utf-8",
        )
        written.append(gj)
    if len(plans) > 1:
        written.append(write_gpkg_layers(_mission_layers(plans), out_dir / "missions.gpkg"))
    return written


_SQL_TYPES = {bool: "BOOLEAN", int: "INTEGER", float: "REAL", str: "TEXT"}


# --------------------------------------------------------------------------- GeoPackage


def gpkg_blob(geom: BaseGeometry, srs_id: int = SRS_ID) -> bytes:
    """GeoPackageBinary: 'GP' + version 0 + flags(LE, XY envelope) + srs_id + envelope + WKB."""
    minx, miny, maxx, maxy = geom.bounds
    header = b"GP" + bytes([0, 0b0000_0011]) + struct.pack("<i", srs_id)
    header += struct.pack("<4d", minx, maxx, miny, maxy)
    return header + shapely.to_wkb(geom, byte_order=1)


def parse_gpkg_blob(blob: bytes) -> BaseGeometry:
    """검증·테스트용 역변환."""
    if blob[:2] != b"GP":
        raise ValueError("GeoPackage 바이너리 헤더가 아님")
    flags = blob[3]
    env = (flags >> 1) & 0b111
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return shapely.from_wkb(blob[8 + env_len :])


def write_gpkg(report: RunReport, path: Path) -> Path:
    layers: dict[str, list[tuple[BaseGeometry, dict[str, Any]]]] = {k: [] for k in LAYER_GEOM}
    for f in report.flights:
        for k, feats in _flight_layers(f).items():
            layers[k].extend(feats)
    for k, feats in _report_layers(report).items():
        layers[k].extend(feats)
    return write_gpkg_layers(layers, path)


def write_gpkg_layers(
    layers: dict[str, list[tuple[BaseGeometry, dict[str, Any]]]], path: Path
) -> Path:
    """레이어 이름 → (지오메트리, 속성) 목록을 GeoPackage 로. 이름은 ``LAYER_GEOM`` 의 키."""
    if path.exists():
        path.unlink()
    wkt = CRS.from_epsg(SRS_ID).to_wkt(WktVersion.WKT1_GDAL)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    con = sqlite3.connect(path)
    try:
        con.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
        con.execute(f"PRAGMA user_version = {GPKG_USER_VERSION}")
        con.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
              srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
              organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
              definition TEXT NOT NULL, description TEXT);
            CREATE TABLE gpkg_contents (
              table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL,
              identifier TEXT UNIQUE, description TEXT DEFAULT '',
              last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
              min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
              CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id)
                REFERENCES gpkg_spatial_ref_sys(srs_id));
            CREATE TABLE gpkg_geometry_columns (
              table_name TEXT NOT NULL, column_name TEXT NOT NULL,
              geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL,
              z TINYINT NOT NULL, m TINYINT NOT NULL,
              CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
              CONSTRAINT uk_gc_table_name UNIQUE (table_name),
              CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
              CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
            """
        )
        con.executemany(
            "INSERT INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)",
            [
                ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined cartesian"),
                ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic"),
                ("WGS 84 geodetic", SRS_ID, "EPSG", SRS_ID, wkt, "longitude/latitude, WGS 84"),
            ],
        )
        for name, feats in layers.items():
            cols = _columns(feats)
            col_sql = ", ".join(f'"{c}" {t}' for c, t in cols)
            con.execute(
                f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, '
                f"geom {LAYER_GEOM[name]}{', ' + col_sql if col_sql else ''})"
            )
            placeholders = ", ".join(["?"] * (1 + len(cols)))
            names = ", ".join(["geom", *(f'"{c}"' for c, _ in cols)])
            con.executemany(
                f'INSERT INTO "{name}" ({names}) VALUES ({placeholders})',
                [(gpkg_blob(g), *[_sql_value(a.get(c)) for c, _ in cols]) for g, a in feats],
            )
            bounds = _bounds(g for g, _ in feats)
            con.execute(
                "INSERT INTO gpkg_contents VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, "features", name, LAYER_TITLE[name], now, *bounds, SRS_ID),
            )
            con.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?,?,?,?,?,?)",
                (name, "geom", LAYER_GEOM[name], SRS_ID, 0, 0),
            )
        con.commit()
    finally:
        con.close()
    return path


def _columns(feats: Sequence[tuple[BaseGeometry, dict[str, Any]]]) -> list[tuple[str, str]]:
    """속성 열 이름·SQL 타입 (첫 등장 순서, None 만 있는 열은 TEXT)."""
    cols: dict[str, str] = {}
    for _, attrs in feats:
        for k, v in attrs.items():
            t = _SQL_TYPES.get(type(v)) if v is not None else None
            if k not in cols or (cols[k] == "TEXT" and t not in (None, "TEXT")):
                cols[k] = t or cols.get(k, "TEXT")
    return list(cols.items())


def _sql_value(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _bounds(
    geoms: Iterable[BaseGeometry],
) -> tuple[float | None, float | None, float | None, float | None]:
    bs = [g.bounds for g in geoms]
    if not bs:
        return None, None, None, None
    return (
        min(b[0] for b in bs),
        min(b[1] for b in bs),
        max(b[2] for b in bs),
        max(b[3] for b in bs),
    )


# --------------------------------------------------------------------------- KML

_KML_NS = "http://www.opengis.net/kml/2.2"
_KML_STYLES = {
    # (선 색 aabbggrr, 선 굵기, 면 색 aabbggrr) — 보고서 지도와 같은 색
    "survey_area": ("ffed3a7c", 3.0, "1aed3a7c"),  # 보라 외곽선 #7C3AED, 옅은 채움
    "coverage": ("ff0c58ea", 1.5, "291673f9"),  # 주황 #EA580C
    "uncovered": ("ff2626dc", 1.5, "592626dc"),  # 빨강 #DC2626
    "track": ("ffaf401e", 2.0, "00000000"),  # 파랑 #1E40AF
    "mission_polygon": ("ffed3a7c", 3.0, "1aed3a7c"),  # 보라 실선 (임무 KMZ)
    "mission_waypoints": ("ff8b5cf6", 1.5, "00000000"),
}


def _ll(coords: Iterable[tuple[float, ...]]) -> str:
    return " ".join(f"{c[0]:.7f},{c[1]:.7f},0" for c in coords)


def _kml_geometry(parent: ET.Element, geom: BaseGeometry) -> None:
    if isinstance(geom, Point):
        p = ET.SubElement(parent, "Point")
        ET.SubElement(p, "coordinates").text = f"{geom.x:.7f},{geom.y:.7f},0"
    elif isinstance(geom, LineString):
        ls = ET.SubElement(parent, "LineString")
        ET.SubElement(ls, "tessellate").text = "1"
        ET.SubElement(ls, "coordinates").text = _ll(geom.coords)
    elif isinstance(geom, Polygon):
        pg = ET.SubElement(parent, "Polygon")
        ob = ET.SubElement(ET.SubElement(pg, "outerBoundaryIs"), "LinearRing")
        ET.SubElement(ob, "coordinates").text = _ll(geom.exterior.coords)
        for ring in geom.interiors:
            ib = ET.SubElement(ET.SubElement(pg, "innerBoundaryIs"), "LinearRing")
            ET.SubElement(ib, "coordinates").text = _ll(ring.coords)
    elif isinstance(geom, MultiPolygon):
        mg = ET.SubElement(parent, "MultiGeometry")
        for g in geom.geoms:
            _kml_geometry(mg, g)


def write_kml(report: RunReport, path: Path) -> Path:
    ET.register_namespace("", _KML_NS)
    kml = ET.Element(f"{{{_KML_NS}}}kml")
    doc = ET.SubElement(kml, "Document")
    ET.SubElement(doc, "name").text = f"FlightQA — {report.root.name}"
    ET.SubElement(doc, "description").text = (
        "survey_area 는 조정기 임무 폴리곤이 아니라 촬영 궤적에서 추정한 구역이다. "
        f"schema_version {report.schema_version}"
    )
    for sid, (line, width, fill) in _KML_STYLES.items():
        st = ET.SubElement(doc, "Style", id=sid)
        ls = ET.SubElement(st, "LineStyle")
        ET.SubElement(ls, "color").text = line
        ET.SubElement(ls, "width").text = f"{width:g}"
        ps = ET.SubElement(st, "PolyStyle")
        ET.SubElement(ps, "color").text = fill
    st = ET.SubElement(doc, "Style", id="captures")
    ic = ET.SubElement(st, "IconStyle")
    ET.SubElement(ic, "scale").text = "0.5"
    ET.SubElement(ic, "color").text = "fff68236"
    for key, feats in _report_layers(report).items():
        for geom, attrs in feats:
            pm = ET.SubElement(doc, "Placemark")
            ET.SubElement(pm, "name").text = LAYER_TITLE[key]
            ET.SubElement(pm, "styleUrl").text = f"#{key}"
            ET.SubElement(pm, "description").text = _kml_desc(attrs)
            _kml_geometry(pm, geom)
    for f in report.flights:
        folder = ET.SubElement(doc, "Folder")
        ET.SubElement(folder, "name").text = f"{f.summary.flight_id} · {f.status.value}"
        layers = _flight_layers(f)
        for key in ("survey_area", "coverage", "uncovered", "track"):
            for geom, attrs in layers[key]:
                pm = ET.SubElement(folder, "Placemark")
                ET.SubElement(pm, "name").text = LAYER_TITLE[key]
                ET.SubElement(pm, "styleUrl").text = f"#{key}"
                ET.SubElement(pm, "description").text = _kml_desc(attrs)
                _kml_geometry(pm, geom)
        sub = ET.SubElement(folder, "Folder")
        ET.SubElement(sub, "name").text = LAYER_TITLE["captures"]
        for geom, attrs in layers["captures"]:
            pm = ET.SubElement(sub, "Placemark")
            ET.SubElement(pm, "name").text = str(attrs["idx"])
            ET.SubElement(pm, "styleUrl").text = "#captures"
            ET.SubElement(pm, "description").text = _kml_desc(attrs)
            _kml_geometry(pm, geom)
    tree = ET.ElementTree(kml)
    ET.indent(tree, space=" ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path


def _kml_desc(attrs: dict[str, Any]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in attrs.items() if v is not None and v != "")


# --------------------------------------------------------------------------- GeoJSON


def write_survey_area_geojson(report: RunReport, path: Path) -> Path:
    import json

    features: list[dict[str, Any]] = []
    for f in report.flights:
        layers = _flight_layers(f)
        for kind in ("survey_area", "uncovered"):
            for geom, attrs in layers[kind]:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": shapely.geometry.mapping(geom),
                        "properties": {"kind": kind, **attrs},
                    }
                )
    for kind, feats in _report_layers(report).items():
        for geom, attrs in feats:
            features.append(
                {
                    "type": "Feature",
                    "geometry": shapely.geometry.mapping(geom),
                    "properties": {"kind": kind, **attrs},
                }
            )
    doc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "schema_version": report.schema_version,
            "root": str(report.root),
            "note": (
                "survey_area.method: mission_kmz = 조정기 임무 폴리곤, "
                "trajectory_hull = 궤적에서 추정한 구역"
            ),
        },
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- GeoTIFF


class RasterGrid:
    """북쪽이 위인 정방 격자 (raster-is-area, 좌상단 모서리 기준)."""

    def __init__(self, minx: float, miny: float, maxx: float, maxy: float, cell: float) -> None:
        self.cell = cell
        self.minx = math.floor(minx / cell) * cell
        self.maxy = math.ceil(maxy / cell) * cell
        self.cols = max(1, math.ceil((maxx - self.minx) / cell))
        self.rows = max(1, math.ceil((self.maxy - miny) / cell))

    @property
    def n_pixels(self) -> int:
        return self.cols * self.rows

    def window(self, geom: BaseGeometry) -> tuple[int, int, int, int]:
        """지오메트리 경계상자를 덮는 (r0, r1, c0, c1), 격자 밖은 잘림."""
        gx0, gy0, gx1, gy1 = geom.bounds
        c0 = max(0, math.floor((gx0 - self.minx) / self.cell))
        c1 = min(self.cols, math.ceil((gx1 - self.minx) / self.cell))
        r0 = max(0, math.floor((self.maxy - gy1) / self.cell))
        r1 = min(self.rows, math.ceil((self.maxy - gy0) / self.cell))
        return r0, r1, c0, c1

    def mask(self, geom: BaseGeometry) -> tuple[tuple[int, int, int, int], np.ndarray]:
        """셀 중심이 지오메트리 안에 있으면 True 인 창 마스크."""
        r0, r1, c0, c1 = self.window(geom)
        if r1 <= r0 or c1 <= c0:
            return (r0, r0, c0, c0), np.zeros((0, 0), dtype=bool)
        xs = self.minx + (np.arange(c0, c1) + 0.5) * self.cell
        ys = self.maxy - (np.arange(r0, r1) + 0.5) * self.cell
        gx, gy = np.meshgrid(xs, ys)
        shapely.prepare(geom)
        inside = shapely.contains_xy(geom, gx.ravel(), gy.ravel()).reshape(gx.shape)
        return (r0, r1, c0, c1), inside

    def geotiff_tags(self, epsg: int) -> list[tuple[int, str, int, Any, bool]]:
        """tifffile extratags (code, dtype, count, value, writeonce). GeoKey: 투영 CRS."""
        keys = [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, epsg]
        return [
            (33550, "d", 3, (self.cell, self.cell, 0.0), True),
            (33922, "d", 6, (0.0, 0.0, 0.0, self.minx, self.maxy, 0.0), True),
            (34735, "H", len(keys), tuple(keys), True),
        ]


def write_geotiff(
    path: Path, arr: np.ndarray, grid: RasterGrid, epsg: int, *, nodata: int | None = None
) -> Path:
    tags = grid.geotiff_tags(epsg)
    if nodata is not None:
        tags.append((42113, "s", 0, str(nodata), True))
    tifffile.imwrite(
        path,
        arr,
        photometric="minisblack",
        compression="zlib",
        metadata=None,
        extratags=tags,
    )
    return path


def write_rasters(report: RunReport, out_dir: Path) -> list[Path]:
    """overlap_count.tif · survey_area.tif. 커버리지가 없으면 빈 목록 (묵은 파일은 지운다)."""
    count_path = out_dir / "overlap_count.tif"
    area_path = out_dir / "survey_area.tif"
    cfg = Config.model_validate(report.config)
    written: list[Path] = []
    if not cfg.survey_area.enabled or report.sensor is None:
        _unlink(count_path, area_path)
        return written
    camera = cfg.sensor(report.sensor).camera()
    epsg = next(
        (
            f.coverage.epsg
            for f in report.flights
            if f.coverage is not None and f.coverage.epsg is not None
        ),
        None,
    )
    if epsg is None:
        _unlink(count_path, area_path)
        return written

    polys: list[Polygon] = []
    areas: list[BaseGeometry] = []
    for f in report.flights:
        f_epsg = f.coverage.epsg if f.coverage is not None else None
        to_xy = None
        if f_epsg != epsg:
            tr = Transformer.from_crs(4326, epsg, always_xy=True)
            to_xy = tr.transform
        polys.extend(p for _, p in footprints(f.rows, f.lines, camera, to_xy))
        if f.survey_area is not None and f.survey_area.geojson:
            tr = Transformer.from_crs(4326, epsg, always_xy=True)
            areas.append(shp_transform(tr.transform, shape(f.survey_area.geojson)))
    if not polys:
        _unlink(count_path, area_path)
        return written

    minx, miny, maxx, maxy = _bounds([*polys, *areas])
    assert minx is not None and miny is not None and maxx is not None and maxy is not None
    cell = cfg.survey_area.raster_cell_m
    grid = RasterGrid(minx, miny, maxx, maxy, cell)
    if grid.n_pixels > cfg.survey_area.raster_max_pixels:
        cell = cell * math.sqrt(grid.n_pixels / cfg.survey_area.raster_max_pixels)
        cell = math.ceil(cell * 10.0) / 10.0
        grid = RasterGrid(minx, miny, maxx, maxy, cell)

    count = np.zeros((grid.rows, grid.cols), dtype=np.uint16)
    for p in polys:
        (r0, r1, c0, c1), m = grid.mask(p)
        if m.size:
            count[r0:r1, c0:c1] += m.astype(np.uint16)
    written.append(write_geotiff(count_path, count, grid, epsg, nodata=0))

    if areas:
        mask = np.zeros((grid.rows, grid.cols), dtype=np.uint8)
        for a in areas:
            (r0, r1, c0, c1), m = grid.mask(a)
            if m.size:
                sub = mask[r0:r1, c0:c1]
                sub[m] = np.where(count[r0:r1, c0:c1][m] > 0, 2, 1).astype(np.uint8)
        written.append(write_geotiff(area_path, mask, grid, epsg, nodata=0))
    else:
        _unlink(area_path)
    return written


def _unlink(*paths: Path) -> None:
    for p in paths:
        if p.exists():
            p.unlink()
