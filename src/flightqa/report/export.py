"""데이터 내보내기 (CLAUDE.md §5.3).

summary.json, captures.csv, lines.csv, excluded.csv, trajectory.geojson 과 GIS 파일
(survey_area.geojson, flightqa.gpkg, flightqa.kml, overlap_count.tif, survey_area.tif) 은
``geo`` 모듈이 쓴다.

스키마는 ``core.models`` 가 단일 출처이고 ``summary.json`` 에 ``schema_version`` 이 들어간다.
좌표(위경도)는 사용자 데이터이므로 이 파일들에만 쓰고 로그에는 남기지 않는다.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from flightqa.core.metashape import MetashapeResult
from flightqa.core.models import CaptureRow, PixelRow, RunReport
from flightqa.report import geo

CAPTURE_COLUMNS = list(CaptureRow.model_fields)
EXPORT_FILES = (
    "summary.json",
    "captures.csv",
    "saturation.csv",
    "lines.csv",
    "excluded.csv",
    "trajectory.geojson",
    "coverage.geojson",
    "survey_area.geojson",
    "flightqa.gpkg",
    "flightqa.kml",
    "overlap_count.tif",
    "survey_area.tif",
)
"""``write_all`` 이 만들 수 있는 파일 이름 (GUI 복사 목록과 공유). 래스터는 커버리지가 있을 때만."""


def write_all(report: RunReport, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        write_summary_json(report, out_dir / "summary.json"),
        write_captures_csv(report, out_dir / "captures.csv"),
        write_lines_csv(report, out_dir / "lines.csv"),
        write_excluded_csv(report, out_dir / "excluded.csv"),
        write_trajectory_geojson(report, out_dir / "trajectory.geojson"),
        write_saturation_csv(report, out_dir / "saturation.csv"),
        write_coverage_geojson(report, out_dir / "coverage.geojson"),
        geo.write_survey_area_geojson(report, out_dir / "survey_area.geojson"),
        geo.write_gpkg(report, out_dir / "flightqa.gpkg"),
        geo.write_kml(report, out_dir / "flightqa.kml"),
        *geo.write_rasters(report, out_dir),
    ]


def write_summary_json(report: RunReport, path: Path) -> Path:
    data = report.model_dump(mode="json")
    # 캡처 행은 CSV 로 나가므로 JSON 에서는 뺀다 (크기).
    for f in data["flights"]:
        f["rows"] = []
        f["pixel_rows"] = []
        if f.get("coverage"):
            f["coverage"]["geojson"] = None  # coverage.geojson 으로 나감
    data["discovery"]["image_files"] = []
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_saturation_csv(report: RunReport, path: Path) -> Path:
    """캡처·밴드별 포화율 (연구 재사용용, CLAUDE.md §4.5)."""
    cols = [*PixelRow.model_fields, "flight_index"]
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for f in report.flights:
            for r in f.pixel_rows:
                row = r.model_dump(mode="json")
                row["flight_index"] = f.summary.index
                w.writerow(row)
    return path


def write_coverage_geojson(report: RunReport, path: Path) -> Path:
    features: list[dict[str, Any]] = []
    for f in report.flights:
        cov = f.coverage
        if cov is None:
            continue
        if cov.geojson:
            features.append(
                {
                    "type": "Feature",
                    "geometry": cov.geojson,
                    "properties": {
                        "kind": "coverage",
                        "flight_id": f.summary.flight_id,
                        "area_m2": cov.area_m2,
                        "n_holes": cov.n_holes,
                    },
                }
            )
        for c in cov.hole_centroids:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": c},
                    "properties": {"kind": "hole", "flight_id": f.summary.flight_id},
                }
            )
    doc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"schema_version": report.schema_version, "root": str(report.root)},
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def write_captures_csv(report: RunReport, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CAPTURE_COLUMNS)
        w.writeheader()
        for f in report.flights:
            for r in f.rows:
                row = r.model_dump(mode="json")
                w.writerow(row)
    return path


def write_lines_csv(report: RunReport, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "flight_index",
                "line",
                "start_capture",
                "end_capture",
                "n_captures",
                "heading_deg",
                "length_m",
                "mean_agl_m",
                "std_agl_m",
                "mean_speed_mps",
                "is_transit",
            ]
        )
        for f in report.flights:
            for ln in f.lines:
                w.writerow(
                    [
                        f.summary.index,
                        ln.index,
                        ln.start_capture,
                        ln.end_capture,
                        ln.n_captures,
                        ln.heading_deg,
                        ln.length_m,
                        ln.mean_agl_m,
                        ln.std_agl_m,
                        ln.mean_speed_mps,
                        ln.is_transit,
                    ]
                )
    return path


def write_excluded_csv(report: RunReport, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["path", "reason"])
        for e in list(report.discovery.ignored) + list(report.excluded):
            w.writerow([str(e.path), e.reason])
    return path


def write_trajectory_geojson(report: RunReport, path: Path) -> Path:
    features: list[dict[str, Any]] = []
    for f in report.flights:
        coords = [[r.lon, r.lat] for r in f.rows if r.lon is not None and r.lat is not None]
        if len(coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "kind": "track",
                        "flight_id": f.summary.flight_id,
                        "status": f.status.value,
                        "n_captures": f.summary.n_captures,
                    },
                }
            )
        for r in f.rows:
            if r.lon is None or r.lat is None:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                    "properties": {
                        "kind": "capture",
                        "flight_id": f.summary.flight_id,
                        "index": r.index,
                        "capture_id": r.capture_id,
                        "time_utc": r.time_utc.isoformat() if r.time_utc else None,
                        "agl_m": r.agl_m,
                        "line": r.line,
                        "flags": r.flags,
                    },
                }
            )
    doc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"schema_version": report.schema_version, "root": str(report.root)},
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def write_metashape(result: MetashapeResult, out_dir: Path, *, stem: str) -> list[Path]:
    """Metashape 보고서 판정 → ``<stem>_metashape.json`` (구조·판정) + ``<stem>_metashape.csv``."""
    from flightqa.core.metashape import to_rows

    out_dir.mkdir(parents=True, exist_ok=True)
    js = out_dir / f"{stem}_metashape.json"
    js.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    cs = out_dir / f"{stem}_metashape.csv"
    rows = to_rows(result)
    with cs.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["key"])
        w.writeheader()
        w.writerows(rows)
    return [js, cs]
