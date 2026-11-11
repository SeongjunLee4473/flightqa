# Metashape 처리 보고서 (PDF) 구조

`core/metashape.py` 의 근거. Agisoft Metashape Professional 2.3.1 (build 22580) 의 **File → Export →
Generate Report** 로 만든 PDF 두 부(2026-10-07)에서 확인했다.

| 샘플 | 카메라 | 쪽 | 특징 |
|---|---|---|---|
| RGB (DJI M3M RGB, 98장) | 1 | 7 | 보정 계수 8개 모두 조정, 위치 오차 cm 단위 |
| 다중분광 (MicaSense RedEdge-M, 1,540장) | 5 (밴드별) | 11 | 보정 페이지 5쪽, K3 고정(오차 없음), 위치 오차 m 단위, Raster Transform |

PDF 는 Qt 5.15 가 만든 **텍스트 PDF** 라 `pypdf.extract_text()` 로 글자가 그대로 나온다. 좌표·현장명은
들어 있지 않다. 실제 PDF 는 저장소에 두지 않고, `tests/fixtures/metashape_text.py` 가 같은 구조의
합성 줄과 합성 PDF 를 만든다.

## 추출 텍스트의 버릇 (정규화 규칙)

- 제목·라벨 줄이 그림자 글씨 때문에 **두 번씩** 나온다 (`Survey Data`, `Camera Model`, …). 연속 중복 줄은
  하나로 합친다. 숫자는 값이 겹칠 수 있어 합치지 않는다.
- 일부 라벨은 글자 사이에 공백이 낀다: `T ie Points`, `Depth M aps`, `Sy stem`, `Raster T ransform`,
  `V a lu e`, `P 1`. 라벨 비교는 공백과 콜론을 뺀 소문자 키로 한다.
- 값은 라벨 **다음 줄**이다 (`Number of images:` → `98`). 같은 라벨이 여러 섹션에 반복되므로
  (`Processing time`, `Software version`, `Resolution`) 섹션 제목 사이 범위 안에서 찾는다.
- 단위가 값에 붙어 있다: `131 m`, `3.52 cm/pix`, `0.222 km²`, `1.08 pix`, `60,000`.

## 쪽 구성

### 1쪽 표지
`Agisoft Metashape` / `Processing Report` / `07 October 2026` (보고서 날짜).

### 2쪽 Survey Data
```
Fig. 1. Camera locations and image overlap.      ← JPEG 그림 (중복도 지도)
Number of images:   98        Flying altitude:  131 m
Ground resolution:  3.52 cm/pix   Coverage area: 0.222 km²
Camera stations:    98        Tie points:       233,346
Projections:        845,599   Reprojection error: 1.08 pix
Camera Model | Resolution | Focal Length | Pixel Size | Precalibrated
M3M (12.29mm) | 5280 x 3956 | 12.29 mm | 3.36 x 3.36 μm | Yes      ← 카메라마다 한 행
Table 1. Cameras.
```
다중분광은 카메라 행이 밴드마다 하나씩이고 이름이 `RedEdge-M, Blue (5.5mm)` 꼴이다. 칸이 좁으면
`RedEdge-M, Red edge (5…` 처럼 **잘려** 나오므로, 보정 페이지의 온전한 이름과는 괄호 앞까지만 비교한다.

### 3쪽~ Camera Calibration (카메라마다 한 쪽)
```
Fig. 2. Image residuals for M3M (12.29mm).        ← 벡터 그림 (추출 안 됨)
M3M (12.29mm)
98 images, precalibrated
Type Resolution Focal Length Pixel Size / Frame 5280 x 3956 12.29 mm 3.36 x 3.36 μm
Value Error  F Cx Cy K1 K2 K3 P1 P2                ← 머리글 (상관 행렬 열 순서)
F   3733.4   0.087  1.00 -0.02 0.09 -0.02 0.08 -0.17 0.00 -0.02
Cx  26.1089  0.022  1.00 0.14 0.03 -0.02 0.02 0.70 0.17
…
Table 2. Calibration coefficients and correlation matrix.
```
텍스트로는 `F, F, 3733.4, 3733.4, 0.087, 1.00, -0.02, …` 순이다 (이름·값이 두 번). 상관 행렬은 상삼각이라
행마다 `1.00` 다음에 **자기 뒤 열**의 값만 온다. 다중분광 샘플은 머리글이 `F Cx Cy K1 K2 P1 P2 K3` 이고
K3 행이 **값만** 있고(고정 계수, 오차·상관 없음) 표 맨 앞에 온다 → 머리글은 "이름, 이름, 숫자" 가
나오기 직전까지, 상관 열은 오차가 있는 계수만으로 센다.

### Camera Locations
```
Fig. 3. Camera locations and error estimates.     ← JPEG 그림
X error (cm) | Y error (cm) | Z error (cm) | XY error (cm) | Total error (cm)
0.799382 0.983384 1.12736 1.2673 1.69617
Table 3. Average camera location error.
```
단위가 괄호에 있다 (RGB·RTK 기체 `cm`, RedEdge 카메라 GPS `m`). 파서는 m 로 환산한다.

### Ground Control Points (샘플에 없음)
GCP 를 쓴 프로젝트에만 나오며 `Control points RMSE` / `Check points RMSE` 요약 행
(Count, X, Y, Z, XY, Total) 을 읽도록만 해 두었다. **[가정]** 실제 GCP 보고서로 확인되면 여기에 기록.

### Digital Elevation Model
`Resolution: 7.03 cm/pix`, `Point density: 202 points/m²`, JPEG 그림 2개(DEM, 색 막대).

### Processing Parameters (1~2쪽)
섹션 순서: `General` (Images, Aligned images, Coordinate system) → `Tie Points` (Points `233,346 of
247,603`, RMS reprojection error `0.228409 (1.08411 pix)` = 정규화 값과 픽셀 값, Max reprojection error,
Mean key point size, Point colors, Average tie point multiplicity) → `Alignment parameters` (Accuracy,
preselection, Key/Tie point limit, Adaptive camera model fitting, 시간·메모리, Software version) →
`Depth Maps` (Count, Quality, Filtering mode, Max neighbors) → `DEM` (Size, Resolution, Coordinate
system, 깊이맵 파라미터 반복) → `Reconstruction parameters` → `Orthomosaic` (Size, Resolution,
Coordinate system, Colors, Orthophotos, Blending mode, Surface, hole filling, ghosting filter) →
`Raster Transform` (다중분광만: `Expression` = `B1 / 32768; …`) → `System` (Software name/version, OS,
RAM, CPU, GPU).

## 파서가 쓰는 항목 ↔ 모델

| `MetashapeReport` | 출처 |
|---|---|
| `n_images`, `flying_altitude_m`, `gsd_cm`, `coverage_km2`, `camera_stations`, `tie_points`, `projections`, `reprojection_error_px` | Survey Data |
| `cameras[].name/band/width_px/height_px/focal_mm/pixel_um/precalibrated` | Table 1. Cameras |
| `cameras[].n_images/coefficients/correlations` | Camera Calibration 쪽 |
| `location_error` (m 환산, `unit` 보존) | Camera Locations |
| `gcp[]` | Ground Control Points (있을 때) |
| `dem` (`resolution`, `point_density`, Size…) | DEM 쪽 + Processing Parameters DEM |
| `n_aligned`, `tie_points_total`, `rms_reprojection_px/norm`, `max_reprojection_px`, `mean_keypoint_size_px`, `tie_point_multiplicity`, `point_colors`, `alignment`, `depth_maps`, `orthomosaic`, `raster_transform`, `software_version`, `coordinate_system` | Processing Parameters |
| `system` | System |
| `figures` | `Fig. N.` 캡션 목록; `extract_figures()` 가 JPEG(DCTDecode) XObject 를 캡션과 짝지어 돌려준다 (첫 쪽 로고·색 막대 제외) |

확인하지 못한 것: Metashape 1.x 보고서의 라벨 차이(예: "Ground resolution" 대신 "Ground sampling distance"),
GCP 표, 구형 보고서의 "Camera stations" 부재. 라벨이 없으면 해당 값은 `None` 이고 판정은 N/A 다.
