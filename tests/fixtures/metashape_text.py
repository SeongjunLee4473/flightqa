"""Metashape 처리 보고서의 텍스트 구조를 흉내 내는 합성 줄 생성기 (docs/metashape_report.md 기준).

실제 보고서 텍스트는 저장소에 넣지 않는다. 여기서는 그림자 중복 줄, 글자 사이 공백("T ie Points"),
잘린 카메라 이름("… (5…"), 값이 두 번 나오는 보정 계수 표, 고정 계수(K3) 를 그대로 재현한다.
``make_pdf`` 는 같은 줄을 담은 진짜 PDF 를 pypdf 로 만들어 추출 경로까지 검증한다.
"""

from __future__ import annotations

from pathlib import Path


def _dup(s: str) -> list[str]:
    return [s, s]


def camera_block(
    name: str,
    n_images: int,
    coefs: list[tuple[str, float, float | None, list[float]]],
    header: list[str],
) -> list[str]:
    """보정 페이지. coefs = (이름, 값, 오차 | None, 상관[자기 제외]) — 고정 계수는 오차 None."""
    out = [
        *_dup("Camera Calibration"),
        "1 pix",
        f"Fig. 2. Image residuals for {name}.",
        *_dup(name),
    ]
    out += [f"{n_images} images, precalibrated", "Type", "Resolution", "Focal Length", "Pixel Size"]
    out += [
        *_dup("Frame"),
        *_dup("1280 x 960"),
        *_dup("5.5 mm"),
        *_dup("3.75 x 3.75 "),
        "μ",
        "μ",
        "m",
        "m",
    ]
    out += [*_dup("V a lu e"), *_dup("Error")]
    for h in header:
        out += _dup(h.replace("P", "P ") if h.startswith("P") else h)
    for name_, value, error, corr in coefs:
        out += _dup(name_.replace("P", "P ") if name_.startswith("P") else name_)
        out += _dup(f"{value}")
        if error is not None:
            out += [f"{error}", "1.00", *[f"{c:.2f}" for c in corr]]
    out += ["Table 2. Calibration coefficients and correlation matrix.", "Page 3"]
    return out


def rgb_report_lines() -> list[str]:
    """RGB 1 카메라 보고서 (M3M 12.29 mm 와 같은 구조, 값은 합성)."""
    lines = [*_dup("Agisoft Metashape"), *_dup("Processing Report"), *_dup("07 October 2026")]
    lines += [
        *_dup("Survey Data"),
        "1",
        "2",
        "3",
        "> 9",
        "100 m",
        "Fig. 1. Camera locations and image overlap.",
    ]
    lines += [
        "Number of images:",
        "98",
        "Flying altitude:",
        "131 m",
        "Ground resolution:",
        "3.52 cm/pix",
    ]
    lines += ["Coverage area:", "0.222 km²", "Camera stations:", "98", "Tie points:", "233,346"]
    lines += ["Projections:", "845,599", "Reprojection error:", "1.08 pix"]
    lines += [
        *_dup("Camera Model"),
        *_dup("Resolution"),
        *_dup("Focal Length"),
        *_dup("Pixel Size"),
    ]
    lines += [
        *_dup("Precalibrated"),
        "M3M (12.29mm)",
        "5280 x 3956",
        "12.29 mm",
        "3.36 x 3.36 ",
        "μ",
        "m",
        "Yes",
    ]
    lines += ["Table 1. Cameras.", "Page 2"]
    header = ["F", "Cx", "Cy", "K1", "K2", "K3", "P1", "P2"]
    coefs: list[tuple[str, float, float | None, list[float]]] = [
        ("F", 3733.4, 0.087, [-0.02, 0.09, -0.02, 0.08, -0.17, 0.00, -0.02]),
        ("Cx", 26.1089, 0.022, [0.14, 0.03, -0.02, 0.02, 0.70, 0.17]),
        ("Cy", -23.2127, 0.02, [0.01, 0.00, -0.01, 0.14, 0.57]),
        ("K1", -0.108282, 3.1e-05, [-0.95, 0.86, 0.03, 0.01]),
        ("K2", -0.00153489, 7.4e-05, [-0.97, -0.04, 0.01]),
        ("K3", -0.0169246, 5.5e-05, [0.04, -0.01]),
        ("P1", -0.000210641, 1.1e-06, [0.03]),
        ("P2", -0.000159111, 1e-06, []),
    ]
    lines += camera_block("M3M (12.29mm)", 98, coefs, header)
    lines += [*_dup("Camera Locations"), "100 m", "-4.00 cm", "4.00 cm", "x 1000"]
    lines += ["Fig. 3. Camera locations and error estimates."]
    lines += [
        "Z error is represented by ellipse color. X,Y errors are represented by ellipse shape."
    ]
    lines += [
        *_dup("X error (cm)"),
        *_dup("Y error (cm)"),
        *_dup("Z error (cm)"),
        *_dup("XY error (cm)"),
    ]
    lines += [*_dup("Total error (cm)"), "0.799382", "0.983384", "1.12736", "1.2673", "1.69617"]
    lines += [
        "Table 3. Average camera location error.",
        "X - Longitude, Y - Latitude, Z - Altitude.",
        "Page 4",
    ]
    lines += [*_dup("Digital Elevation Model"), "-50.0 m", "120 m", "100 m"]
    lines += ["Fig. 4. Reconstructed digital elevation model.", "Resolution:", "7.03 cm/pix"]
    lines += ["Point density:", "202 points/m²", "Page 5"]
    lines += processing_lines(
        98, 98, "3 bands, uint8", tie="233,346 of 247,603", rms="0.228409 (1.08411 pix)"
    )
    lines += ["Max reprojection error", "0.686589 (44.6458 pix)"]  # 순서 무관하게 찾는다
    return lines


def processing_lines(
    images: int, aligned: int, colors: str, *, tie: str, rms: str, raster: str | None = None
) -> list[str]:
    out = [
        *_dup("Processing Parameters"),
        *_dup("General"),
        "Images",
        f"{images}",
        "Aligned images",
        f"{aligned}",
    ]
    out += ["Coordinate system", "WGS 84 (EPSG::4326)", "Rotation angles", "Yaw, Pitch, Roll"]
    out += [*_dup("T ie Points"), "Points", tie, "RMS reprojection error", rms]
    out += [
        "Max reprojection error",
        "0.686589 (44.6458 pix)",
        "Mean key point size",
        "4.09121 pix",
    ]
    out += ["Point colors", colors, "Key points", "No", "Average tie point multiplicity", "3.99345"]
    out += [*_dup("Alignment parameters"), "Accuracy", "High", "Generic preselection", "Yes"]
    out += [
        "Reference preselection",
        "Source",
        "Key point limit",
        "60,000",
        "Key point limit per Mpx",
        "3,000",
    ]
    out += [
        "Tie point limit",
        "10,000",
        "Exclude stationary tie points",
        "Yes",
        "Guided image matching",
        "No",
    ]
    out += ["Adaptive camera model fitting", "Yes", "Matching time", "1 minutes 2 seconds"]
    out += [
        "Alignment time",
        "27 seconds",
        "Date created",
        "2026:10:07 15:58:03",
        "Software version",
        "2.3.1.22580",
    ]
    out += [*_dup("Depth M aps"), "Count", f"{aligned}", *_dup("Depth maps generation parameters")]
    out += [
        "Quality",
        "High",
        "Filtering mode",
        "Mild",
        "Max neighbors",
        "16",
        "Processing time",
        "5 minutes 9 seconds",
    ]
    out += [
        *_dup("DEM"),
        "Size",
        "8,370 x 9,473",
        "Resolution",
        "7.03 cm/pix",
        "Coordinate system",
        "WGS 84 (EPSG::4326)",
    ]
    out += [
        *_dup("Reconstruction parameters"),
        "Source data",
        "Depth maps",
        "Interpolation",
        "Enabled",
        "Page 6",
    ]
    out += [*_dup("Orthomosaic"), "Size", "16,740 x 18,946", "Resolution", "3.52 cm/pix"]
    out += ["Coordinate system", "WGS 84 (EPSG::4326)", "Colors", colors, "Orthophotos", "1.20 GB"]
    out += [
        *_dup("Reconstruction parameters"),
        "Blending mode",
        "Mosaic",
        "Surface",
        "DEM",
        "Enable hole filling",
        "Yes",
    ]
    if raster:
        out += [*_dup("Raster T ransform"), "Expression", raster]
    out += [
        *_dup("Sy stem"),
        "Software name",
        "Agisoft Metashape Professional",
        "Software version",
        "2.3.1 build 22580",
    ]
    out += [
        "OS",
        "Windows 64 bit",
        "RAM",
        "63.94 GB",
        "CPU",
        "Example CPU",
        "GPU(s)",
        "Example GPU",
        "Page 7",
    ]
    return out


BANDS = ["Blue", "Green", "Red", "Red edge", "NIR"]


def ms_report_lines(*, aligned: int = 1520) -> list[str]:
    """RedEdge 5 밴드 보고서: 잘린 이름, 고정 K3(오차 없음, 머리글 마지막·행 첫째), 위치 오차 m."""
    lines = [*_dup("Agisoft Metashape"), *_dup("Processing Report"), *_dup("02 October 2026")]
    lines += [*_dup("Survey Data"), "Fig. 1. Camera locations and image overlap."]
    lines += [
        "Number of images:",
        "1,540",
        "Flying altitude:",
        "82.1 m",
        "Ground resolution:",
        "5.67 cm/pix",
    ]
    lines += [
        "Coverage area:",
        "0.130 km²",
        "Camera stations:",
        "1,520",
        "Tie points:",
        "1,112,381",
    ]
    lines += ["Projections:", "3,956,552", "Reprojection error:", "0.533 pix"]
    lines += [
        *_dup("Camera Model"),
        *_dup("Resolution"),
        *_dup("Focal Length"),
        *_dup("Pixel Size"),
        *_dup("Precalibrated"),
    ]
    for b in BANDS:
        name = "RedEdge-M, Red edge (5…" if b == "Red edge" else f"RedEdge-M, {b} (5.5mm)"
        lines += [name, "1280 x 960", "5.5 mm", "3.75 x 3.75 ", "μ", "m", "Yes"]
    lines += ["Table 1. Cameras.", "Page 2"]
    header = ["F", "Cx", "Cy", "K1", "K2", "P1", "P2", "K3"]
    for i, b in enumerate(BANDS):
        coefs: list[tuple[str, float, float | None, list[float]]] = [
            ("K3", -0.075 - i * 0.001, None, []),
            ("F", 1448.37 + i, 0.1, [-0.04, -0.16, 0.08, 0.17, -0.03, -0.08]),
            ("Cx", -1.84821, 0.034, [-0.01, -0.03, 0.00, 0.89, 0.01]),
            ("Cy", 4.80854, 0.028, [-0.04, -0.01, 0.00, 0.79]),
            ("K1", -0.0996897, 9e-05, [-0.89, -0.03, -0.05]),
            ("K2", 0.169629, 0.00029, [-0.00, -0.01]),
            ("P1", -0.000447088, 7.4e-06, [0.01]),
            ("P2", -4.56941e-05, 6e-06, []),
        ]
        lines += camera_block(f"RedEdge-M, {b} (5.5mm)", 308, coefs, header)
    lines += [*_dup("Camera Locations"), "100 m", "Fig. 7. Camera locations and error estimates."]
    lines += [
        *_dup("X error (m)"),
        *_dup("Y error (m)"),
        *_dup("Z error (m)"),
        *_dup("XY error (m)"),
        *_dup("Total error (m)"),
    ]
    lines += [
        "0.493094",
        "0.917086",
        "0.39973",
        "1.04124",
        "1.11534",
        "Table 7. Average camera location error.",
        "Page 8",
    ]
    lines += [*_dup("Digital Elevation Model"), "Fig. 8. Reconstructed digital elevation model."]
    lines += ["Resolution:", "11.3 cm/pix", "Point density:", "77.7 points/m²", "Page 9"]
    lines += processing_lines(
        1540,
        aligned,
        "5 bands, uint16",
        tie="1,112,381 of 1,243,379",
        rms="0.15916 (0.532517 pix)",
        raster="B1 / 32768; B2 / 32768; B3 / 32768; B4 / 32768; B5 / 32768",
    )
    return lines


def make_pdf(path: Path, lines: list[str], per_page: int = 60) -> Path:
    """줄 목록을 담은 진짜 PDF (Helvetica, WinAnsi) — pypdf 저수준 객체로 만든다.

    비 ASCII 글자(μ, ², ·)는 글꼴에 없어 '?' 로 바꾼다; 파서는 그 글자를 쓰지 않는다.
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        StreamObject,
    )

    w = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    font_ref = w._add_object(font)
    for i in range(0, len(lines), per_page):
        chunk = lines[i : i + per_page]
        page = w.add_blank_page(width=595, height=842)
        ops = ["BT", "/F1 9 Tf", "40 810 Td", "11 TL"]
        for ln in chunk:
            safe = ln.encode("cp1252", "replace").decode("cp1252")
            esc = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*")
        ops.append("ET")
        stream = StreamObject()
        stream._data = "\n".join(ops).encode("cp1252", "replace")
        page[NameObject("/Contents")] = w._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        page[NameObject("/MediaBox")] = ArrayObject(
            [FloatObject(0), FloatObject(0), NumberObject(595), NumberObject(842)]
        )
    with path.open("wb") as fh:
        w.write(fh)
    return path
