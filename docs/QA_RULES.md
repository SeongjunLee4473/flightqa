# QA_RULES — 판정 규칙과 임계값 근거

모든 임계값은 `src/flightqa/config/default.toml` 에 있고 사용자 TOML 로 덮어쓸 수 있다.
이 문서는 **왜 그 값인지** 를 적는다. 근거가 문헌이 아니라 운용 기준(CLAUDE.md §2.1)이면 그렇게 표시한다.
상태: PASS / WARN / FAIL / N/A. **게이트** 항목이 FAIL 이면 전체가 "재비행 검토" 가 된다 (§4.8).

버전: M1 (2026-09-10). §4.4 중복도, §4.5 포화, §4.6 조도·태양, §4.7 패널은 M2 에서 추가.

---

## 판정 합산 규칙 (`core/rules.py`)

| 규칙 | 내용 | 근거 |
|---|---|---|
| 게이트 | `gate=True` 항목이 FAIL → 비행 FAIL, 전체 FAIL ("재비행 검토 필요") | 후처리 불가 조건은 나머지가 좋아도 의미 없음 |
| FAIL 전파 | 게이트가 아니어도 FAIL 항목이 하나라도 있으면 비행 FAIL | 항목별 FAIL 임계값은 이미 "그대로 쓰면 안 됨" 수준으로 설정 |
| WARN 누적 | WARN 이 `judgement.warn_to_fail_count` (6) 개 이상이면 FAIL | 개별로는 경미해도 누적되면 결과 품질을 보장 못 함. 값은 운용 기준 |
| 전체 | 비행들과 데이터셋 단위(파일 상태) 중 최악 | — |
| 권고 조치 | WARN/FAIL 항목마다 구체적 행동 문장 | CLAUDE.md §4.8 |

정보 항목(기준 없음)은 값이 있으면 PASS, 없으면 N/A 로 두어 합산에 영향을 주지 않는다.

---

## 파일 상태 (데이터셋 단위, `analysis/integrity.analyze_files`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| files.corrupt | 0 바이트·헤더 손상·exiftool 오류 파일 수 | ≥ 1 개 | ≥ 2 % | — | 손상 1 개도 카드 오류 신호. 2 % 는 후처리 투입 전 재복사가 필요한 수준 (운용 기준) |
| files.duplicates | 같은 캡처 ID·밴드 조합의 중복 파일 | ≥ 1 개 | — | — | 복사본이 섞이면 Metashape 가 같은 위치를 두 번 정렬 |
| files.unresolved | 파일명 규칙 불일치 이미지 (정보) | — | — | — | RedEdge 8.3 이름(`IM1ABD~1.TIF`) 실측. 태그로 편입 시도 후 결과만 보고 |

## 캡처 무결성 (비행 단위, `analysis/integrity.analyze_integrity`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| integrity.missing_bands | 밴드 파일이 빠진 캡처 비율 | ≥ 0.5 % | ≥ 5 % | **예** | 불완전 캡처는 후처리에서 통째로 빠져 그 위치의 중복이 준다. 5 % 는 3,000 캡처 중 150 회 → 라인 하나 분량 (운용 기준) |
| integrity.interval_median | 캡처 간격 중앙값·p95·최대 (정보) | — | — | — | 계획 간격(RedEdge ~3.8 s, M3M ~2 s) 대조용 |
| integrity.gaps | 간격 > 중앙값 × `interval_outlier_factor`(2) 인 공백 수 | ≥ 1 | ≥ 10 | — | 2 배 공백 = 캡처 1 회 누락 상당 → 종중복 75 % 가 50 % 로 떨어짐 (CLAUDE.md §4.2) |
| integrity.clock_reversal | 시각이 직전 캡처보다 앞선 횟수 | ≥ 1 | — | — | GPS 시각 동기 실패 신호. 정렬 오류로 이어짐 |
| integrity.time_mismatch | 파일 mtime–EXIF 시각 불일치 파일 비율 (허용 120 s, UTC/로컬 관례 보정) | ≥ 5 % | — | — | 복사·편집으로 갱신된 파일 검출. RedEdge 는 UTC 숫자를 FAT 로컬 시각에 쓰므로 ±UTC 오프셋을 정상으로 본다 (docs/ASSUMPTIONS.md) |
| integrity.n_captures | 캡처 수, 완전/불완전 (정보) | — | — | — | — |

## 비행 궤적·고도 (비행 단위, `analysis/trajectory.analyze_trajectory`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| flight.agl_mean | 조사 라인 캡처의 평균 AGL | 목표 ± 20 % 초과 | ± 30 % 초과 | — | CLAUDE.md §2.1 "고도 허용 오차 목표고도의 20–30 %". **목표의 출처**(2026-10-19): `[flight].target_agl_m` 숫자 → 비행에 짝지어진 임무 KMZ 의 촬영 고도 → 둘 다 없으면 조사 라인 AGL 중앙값. 중앙값이 기준일 때는 평균 자체는 정보 항목(PASS) 이고 일관성은 아래 항목이 본다. 현장마다 임무 고도가 다르므로(50·120·300 m) 고정 목표를 기본값으로 두면 모든 비행이 FAIL 로 오판됨 (실측) |
| flight.agl_within_tolerance | 기준 고도 ± 20 % 이내 캡처 비율 | < 90 % | < 70 % | **예** | 평균은 맞아도 구간별 이탈이 크면 GSD·중복이 불균일. 70 % 미만이면 정사영상 품질 보장 불가 (운용 기준). 기준 고도는 위 항목과 같은 출처 |
| flight.altitude_std | 라인별 고도 표준편차의 중앙값 | ≥ 5 m | ≥ 10 m | — | 120 m AGL 에서 5 m = GSD 4 % 변동. 10 m 이상은 지형추종 실패·강풍 의심 (운용 기준) |
| flight.speed | 조사 라인 비행속도 중앙값 (연속 캡처 거리/시간) | 목표 ± 25 % | ± 50 % | — | 속도 ↑ = 종중복 ↓ 및 모션 블러. 5 m/s × 3.8 s = 19 m 간격 기준 (CLAUDE.md §2.1). 목표의 출처는 `[flight].target_speed_mps` 숫자 → 임무 KMZ 의 비행속도 → 없으면 정보 항목 |
| flight.off_nadir | 연직 이탈 > 5° 인 캡처 비율 / p95 각 (경사 촬영 구간 제외) | 비율 > 10 % | p95 > 10° | — | 5° 기울기는 120 m 에서 지상 10 m 시차. M3M 은 짐벌각, RedEdge 는 DLS 자세(고정 마운트). DJI 의 GimbalRoll ±180° 표기는 요 뒤집힘이므로 롤을 ±90° 로 접는다 (실측) |
| flight.oblique | 의도된 경사 촬영 캡처 수 (정보, 2026-10-19) | — | — | — | `oblique_min_deg`(30°) 이상 기울어진 캡처가 `oblique_min_run`(3) 개 이상 이어지면 DJI 고도 최적화(임무 끝에 짐벌 45° 로 중심을 향해 촬영)처럼 절차상의 경사 촬영으로 본다. 연직 이탈·조사 라인·중복도·조사 구역에서 제외하고 건수만 보인다 (실측: 136 캡처 중 끝 10장, 45.1°). 후처리에는 그대로 투입된다 |
| flight.terrain_follow | 지형추종(DSM 추종) 비행 추정 (정보, 2026-09-11) | — | — | — | 아래 "지형추종 감지" 참조. 예 이면 agl_mean·agl_within_tolerance·altitude_std 를 **N/A** 로 두어 게이트에서 제외 |
| flight.lines | 조사 라인 수, 전환 구간 수, 라인 간격 추정 (정보) | — | — | — | 라인 = 진행방향 45° 이상 꺾임 또는 큰 시간 공백으로 분리, `min_captures_per_line`(4) 미만은 전환 |
| flight.track_length | 궤적 길이 (정보, UTM 투영) | — | — | — | — |
| rtk.fixed_pct (M3M) | RtkFlag ∈ `fixed_flags`(50) 인 캡처 비율 | < 95 % | < 80 % | **예** | RTK Fix 아니면 위치 정확도 cm → m 급. GCP 없는 산림 조사에서 절대정확도의 근거가 사라짐. 50 = Fix 는 가정 (docs/ASSUMPTIONS.md) |
| rtk.fixed_pct (RedEdge) | N/A | — | — | — | Quick Mount 는 기체 RTK 를 영상에 주입하지 않음 (CLAUDE.md §2.1). 카메라 GPS XY 정확도 중앙값을 참고로 표시 |

### AGL 기준 (CLAUDE.md §4.3)

1. M3M: `RelativeAltitude` (기체 기록, 이륙점 기준). 지형 기복은 반영 안 됨.
2. RedEdge: `GPS 고도 − 이륙점 고도`. 이륙점은 설정 `takeoff_alt_msl_m`, 없으면 **첫 캡처 고도** (지상 패널 촬영 가정).
   첫 캡처가 이미 비행고도(중앙 고도 − 첫 캡처 < 목표의 25 %)면 이륙점을 알 수 없으므로 AGL 항목을 **N/A** 로 두고 경고한다. FAIL 로 오판하지 않기 위함.
3. `dem_path` 에 GeoTIFF DEM/DSM 을 주면 **실제 AGL = 기체 고도 − `geoid_offset_m` − 지표고도** (`analysis/dem.py`, 쌍선형 보간, 2026-09-11 구현).
   DJI `AbsoluteAltitude`(RtkAlt) 는 타원체고이고 국내 DEM 은 정표고이므로 지오이드고(N ≈ 한반도 중부 +25 m)를 설정으로 보정한다. DEM 이 캡처의 절반 이상을 덮지 못하면 이륙점 기준으로 되돌리고 경고한다.

산림지는 수관 높이 보정이 불가하므로 보고서에 "이륙점 기준 상대고도" 임을 항상 명시한다.

### 지형추종 감지 (2026-09-11, `flight.terrain_follow_*`)

DJI Pilot 의 DSM 추종(지형 추종) 미션은 실제 AGL 을 일정하게 유지하지만 영상에 기록되는 `RelativeAltitude` 는 이륙점 기준이라 지형을 따라 오르내린다. 이를 이륙점 기준으로 판정하면 정상 비행이 AGL FAIL 로 오판된다 (실제 M3M 미션에서 발생: 이륙점 기준 29–73 m, 허용범위 이내 0 %).
DEM 없이 데이터만으로 두 신호를 함께 보아 감지한다 (둘 다 만족해야 함):

| 신호 | 기준 (기본값) | 근거 |
|---|---|---|
| 라인 안 고도 변화폭 (p95−p5) 의 중앙값 | ≥ `terrain_follow_min_range_m` (5 m) | RTK 고도 유지 비행은 cm 급이라 한 라인 안에서 수 m 를 넘지 않는다 |
| 인접 조사 라인 고도 프로파일(진행축 좌표 대비 고도, 20 점 재표본)의 피어슨 상관 중앙값 | ≥ `terrain_follow_corr_min` (0.8) | 같은 지형을 왕복하면 같은 굴곡이 반복된다. 바람·고도 유지 실패는 라인마다 무작위라 상관이 낮다 (실제 미션 r = 0.99, 합성 무작위 고도 r ≈ 0) |

감지되면 AGL 세 항목을 N/A 로 두고, 근거에 변화폭·상관·이륙점 기준 고도 범위를 적으며, 권고 조치으로 DSM 추종 설정 확인과 `dem_path` 지정을 안내한다. `terrain_follow_detect = false` 로 끌 수 있고, DEM 을 지정하면(실제 AGL) 감지를 건너뛴다. 조사 라인이 8 캡처 이상인 것이 2 개 이상 있어야 판정한다.

### PPK 원시파일 힌트 (2026-09-11)

미션 폴더에 DJI RTK 기체의 PPK 원시파일(`*_Timestamp.MRK`, `*PPKOBS.obs`, `*PPKRAW.bin`, `*PPKNAV.nav`)이 있으면 `rtk.fixed_pct` 가 WARN/FAIL 일 때 권고 조치에 "DJI Terra PPK 후처리로 복구 가능" 을 덧붙인다. 네트워크 RTK 가 끊긴 구간은 재비행 없이 복구되는 경우가 많기 때문 (판정 자체는 바꾸지 않는다).

---

## M2 추가 (2026-09-10)

버전: M2. §4.4 중복도, §4.5 노출·포화, §4.6 조도·태양, §4.7 패널.

### 중복도·커버리지 (`analysis/overlap.py`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| overlap.forward | 같은 조사 라인의 연속 캡처 쌍 종중복 중앙값 = (along-track 길이 − 거리)/길이. 카메라 물리값 × **실제 AGL** | < 75 % 또는 기준 충족 쌍 < 90 % | < 60 % | **예** | CLAUDE.md §2.1 "종중복 75 % 이상". 60 % 미만은 SfM 정렬 실패 위험 (운용 기준). 이미지 폭(width) 축이 진행방향에 수직이라고 본다 |
| overlap.side | (across-track 폭 − 실측 라인 간격)/폭 | < 60 % | < 40 % | — | 산림 정사영상 관행 (측중복 60–70 %). 40 % 미만은 라인 사이 정렬 불량 |
| overlap.area | 커버리지 합집합 면적 (정보) | — | — | — | shapely unary_union |
| overlap.holes | 합집합 내부 공백(≥ 50 m²) 수 | ≥ 1 | — | — | 공백 위치는 coverage.geojson 의 hole 포인트 |
| overlap.gsd | 실측 AGL 기준 GSD 중앙값 (정보) | — | — | — | — |
| overlap.survey_area | 조사 구역 면적 (정보). **임무 KMZ** 가 있으면 조정기 매핑 폴리곤 그대로 (`method = mission_kmz`), 없으면 **추정**: 조사 라인 캡처점(전환·지상 제외)의 오목 껍질(`hull_ratio` 0.3) + 여유 폭(커버리지 짧은 변 × `margin_frac`, 기본 0) | — | — | — | 조정기 임무 폴리곤은 영상 EXIF/XMP 에 없다 (ASSUMPTIONS 2026-09-30). 실제 Pilot 2 매핑 임무 1건에서 점 껍질과 폴리곤의 IoU 가 여유 0 m 에서 0.88 로 최대, 여유를 줄수록 하락 → 기본 0 (`analysis/survey_area.py`, `core/mission.py`, `docs/tags_dji_wpml.md`) |
| overlap.survey_fill | (커버리지 합집합 ∩ 구역) / 구역 | < 95 % | < 85 % | — | 미촬영 조각(≥ 50 m²)은 `survey_area.geojson` 의 uncovered, `survey_area.tif` 값 1, `flightqa.gpkg` 의 uncovered 레이어. 실측: RedEdge 18 라인 격자 100 %, M3M 20 라인 DSM 추종 98.2 % (1 조각 1,128 m²), M3M 12 라인 + KMZ 폴리곤 100 % |
| overlap.survey_outside | 임무 폴리곤 밖 조사 라인 캡처 비율 (정보, KMZ 있을 때만) | — | — | — | Pilot 2 는 라인을 폴리곤 밖으로 `margin`(기본 10 m) 만큼 늘리므로 라인 끝 캡처는 정상적으로 밖에 있다. 실측 15.8 % (12 라인 × 양끝 + 여유). 값이 크게 뛰면 폴리곤·비행 불일치(다른 임무 파일) 의심 |

경계값 규칙: "이상"으로 정의된 하한(75 %, 95 % 등)은 값이 정확히 경계이면 통과한다 (`grade_min` 은 `<`).

### 노출·포화 (`analysis/exposure.py`, 픽셀 통계 `analysis/pixels.py`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| exposure.saturated_captures | 한 밴드라도 포화 픽셀(DN ≥ 65520) ≥ 0.5 % 인 캡처의 비율 (지상 캡처 제외) | ≥ 3 % | ≥ 15 % | **예** | 0.5 % = 1280×960 에서 6,144 픽셀, 밝은 지물 하나 크기. 포화 픽셀은 반사율이 잘려 식생지수를 왜곡. 15 % 는 라인 여러 개가 영향받는 수준 (운용 기준) |
| exposure.underexposed | 모든 밴드에서 p99−p1 < 다이내믹 레인지 5 % 인 캡처 비율 | ≥ 5 % | — | — | 12-bit 에서 5 % = 205 DN, 양자화 잡음 대비 신호가 작음 |
| exposure.jumps | 연속 캡처 간 노출시간 ≥ 2 배 변화 횟수 | ≥ 3 | — | — | 자동노출이 구름·밝은 지물에 반응한 흔적. 조도 항목과 대조 |
| exposure.summary | 밴드별 노출시간·ISO 중앙값 (정보) | — | — | — | — |
| exposure.read_errors | 픽셀 읽기 실패 파일 수 | ≥ 1 | — | — | 손상 파일 |

픽셀 통계는 `stride` 로 서브샘플링할 수 있다(`--fast` = 4 → 1/16 픽셀). 포화율의 해상도는 stride² 픽셀당 1 개가 된다.

### 조도·태양 (`analysis/irradiance.py`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| irradiance.cv | 조사 구간 밴드 평균 조도의 변동계수 | ≥ 10 % | ≥ 25 % | — | 맑은 날 30 분 비행의 조도 변화는 수 %. 10 % 이상은 구름, 25 % 이상은 반사율 보정 한계 (운용 기준) |
| irradiance.events | 이동 중앙값(15 캡처) 의 75 % 미만으로 떨어진 구간 수 | ≥ 1 | ≥ 5 | — | 구름 통과 1 회 = 그 라인의 반사율 불연속 |
| irradiance.stable_fraction | 이동 중앙값 ±10 % 이내 캡처 비율 | < 80 % | — | — | — |
| irradiance.symmetry | 처음 10 % vs 마지막 10 % 평균 조도 차 | ≥ 20 % | — | — | 전/후 패널 보간 보정의 필요성 지표 |
| irradiance.solar_elevation | pvlib 겉보기 태양고도 < 30° 인 캡처 비율 | ≥ 10 % | ≥ 50 % | — | CLAUDE.md §4.6 기본 30°. 낮은 태양고도 = 긴 그림자·강한 BRDF |
| irradiance.xmp_solar_check | RedEdge DLS 기록 태양고도 vs pvlib 계산 중앙 차 | ≥ 5° | — | — | 실제 샘플에서 차이 0.00° (실측). 어긋나면 카메라 시각·GPS 오류 |

RedEdge 조도 = `XMP-DLS:HorizontalIrradiance` 밴드 평균 (W/m²/nm). M3M = `Irradiance / (IrradianceExposureTime × IrradianceGain)` (가정, ASSUMPTIONS.md). 둘 다 절대값이 아니라 비행 내 상대 변동만 판정한다.

### 캘리브레이션 패널 (`analysis/panel.py`)

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| panel.pre | 첫 조사 라인 전 20 분 내 지상(AGL ≤ 5 m) 캡처 수 | (M3M: 없으면 N/A) | RedEdge: 없으면 FAIL | **예 (RedEdge)** | MicaSense 반사율 변환의 기준. M3M 은 DJI Terra 가 조도 센서로 보정하므로 선택 (`[panel].required_for`) |
| panel.post | 마지막 조사 라인 후 20 분 내 지상 캡처 수 | RedEdge: 없으면 WARN | — | — | 전/후 두 패널이 있어야 시간 보간 보정 가능 |
| panel.saturation | 패널 후보 캡처의 어느 밴드라도 포화 픽셀 ≥ 0.5 % | — | ≥ 1 캡처 | **예** | 포화된 패널은 기준으로 쓸 수 없음 (CLAUDE.md §4.7 "치명적") |

"패널로 추정" 힌트: 밝은 영역(p50–p99 중간값 이상) 비율 1–50 %, 경계상자 채움률 ≥ 0.6, 영역 내 변동계수 ≤ 0.15 를 밴드 과반이 만족. 정밀 검출은 비범위이며 판정에 쓰지 않고 표시만 한다.

---

## 후처리 품질 — Metashape 처리 보고서 (`core/metashape.py`, `[metashape]`, 2026-10-07)

Metashape 의 **Generate Report** PDF 를 읽어 후처리 결과가 쓸 만한지 판정한다. 비행 QA 와 별개 화면(후처리 품질, Ctrl+5)·명령(`flightqa metashape`)이고, 합산 규칙은 비행과 같다(게이트 FAIL → "재처리 검토 필요", WARN 누적 `warn_to_fail_count`=4). 보고서 구조는 [metashape_report.md](metashape_report.md). 임계값은 Metashape 2.3 보고서 두 부(RGB M3M, RedEdge 5 밴드)와 Agisoft 가이드의 통상 권고를 바탕으로 한 **초기값**이며, 현장 프로젝트가 쌓이면 재조정한다.

### 정렬·타이포인트

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| ms.aligned | 정렬된 영상 / 전체 영상 (%) | < 100 % | < 95 % | **예** | 미정렬 영상은 정사영상에서 통째로 빠진다. 한 장만 빠져도 어느 장인지 확인할 가치가 있어 WARN. 5 % 이상이면 그 구간은 재촬영·재처리 대상 (운용 기준). 다중분광은 캡처 × 밴드 수로 환산해 보여 준다 |
| ms.reproj_rms | RMS 재투영 오차 (px) | > 1.0 | > 1.5 | — | Agisoft 권고는 1 px 이하. 1.5 px 를 넘으면 타이포인트 정리(Gradual selection)·재최적화가 필요 |
| ms.reproj_max | 최대 재투영 오차 (px) | > 30 | — | — | 극단값은 잘못 매칭된 타이포인트가 남았다는 신호. 재구성에는 큰 영향이 없어 WARN 만 |
| ms.multiplicity | 타이포인트 평균 관측 영상 수 (관측 영상 수) | < 3.0 | — | — | 3 미만이면 중복이 부족해 점이 2 장에서만 보인 것. 중복도 QA 와 짝을 이루는 후처리 측 지표 |
| ms.alignment_accuracy | Alignment accuracy 설정 | Low 이하 | — | — | 정보성. Lowest/Low 는 축소 영상으로 매칭해 정밀도가 떨어진다 |

### 카메라 보정

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| ms.calib_corr | 보정 계수 간 상관 절댓값 최대 (카메라별 상관 행렬) | ≥ 0.95 | — | — | K1·K2·K3 끼리 0.95 이상이면 계수가 과매개(over-parameterised) 된 것. K3 또는 K4 를 고정하고 재최적화하면 안정된다 (Agisoft 포럼·가이드의 통상 권고). RGB 샘플은 K2–K3 = −0.97 로 WARN, 다중분광은 K3 고정이라 0.89 로 PASS |
| ms.precalibrated | Precalibrated 사용 여부 | — | — | — | 정보성. 다중분광은 공장 보정값을 쓰는 것이 보통 |
| ms.focal_error | 초점거리 F 의 표준오차 (px) | > F 의 0.1 % | — | — | 보정이 수렴하지 않았다는 신호 |
| ms.cameras | 카메라(밴드) 수와 각 n_images | — | — | — | 정보성. 밴드별 영상 수가 다르면 밴드 누락 |

### 위치 정확도

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| ms.loc_xy | 평균 카메라 위치 오차 XY (m) | RTK 0.10 / GPS 3.0 | RTK 0.30 / GPS 10 | — | 프로필은 `location_profile`: `auto` 는 카메라 이름이 MicaSense/RedEdge 면 GPS(카메라 자체 GPS, Quick Mount 라 RTK 미주입, CLAUDE.md §2.1), 그 외(M3M 등 RTK 기체)는 RTK. RTK 는 cm 급이 정상이라 10 cm 를 넘으면 RTK 가 끊긴 구간이 있다는 뜻. 카메라 GPS 는 수 m 가 정상 |
| ms.loc_z | 평균 카메라 위치 오차 Z (m) | RTK 0.15 / GPS 5 | RTK 0.50 / GPS 15 | — | Z 는 XY 보다 1.5–2 배 나쁜 것이 보통 |
| ms.gcp_control / ms.gcp_check | GCP·검사점 RMSE | XY > GSD×2, Z > GSD×3 | — | — | 보고서에 GCP 표가 있을 때만. 항공사진측량 작업규정의 "GSD 의 2–3 배" 관행. 샘플에 GCP 가 없어 **미검증** |

### 산출물·설정

| 키 | 측정 | WARN | FAIL | 게이트 | 근거 |
|---|---|---|---|---|---|
| ms.gsd | Ground resolution (cm/px) | — | — | — | 정보성 (비행고도·면적 함께) |
| ms.depth_quality | Depth maps quality | Low 이하 | — | — | Lowest/Low 는 DEM·정사영상의 세부가 뭉개진다 |
| ms.dem / ms.ortho | DEM·정사영상 해상도·크기·좌표계·블렌딩 | — | — | — | 정보성 |
| ms.reflectance | Raster Transform(반사율 변환식) 존재 | 없으면 WARN | — | — | 다중분광만. 변환식이 없으면 정사영상이 DN 이라 식생지수를 바로 계산할 수 없다 |
| ms.software | Metashape 버전·OS·GPU | — | — | — | 정보성 (재현성 기록) |

### 비행 분석과 대조 (선택, `--flight` / 화면의 "비행 분석 대조")

같은 현장의 FlightQA 결과(`summary.json`)를 고르면 추가되는 **진단용** 그룹이다. 항목은 FAIL 까지 나오지만 전체 판정에는 WARN 까지만 반영하고 WARN 누적에도 넣지 않는다 — 불일치는 "재처리" 가 아니라 "다른 비행이 짝지어졌거나 정의가 다름" 을 뜻하기 때문.

| 키 | 측정 | WARN | FAIL | 근거 |
|---|---|---|---|---|
| ms.x_images | 보고서 영상 수 vs 캡처 × 밴드 수 | 차이 > 1 % | > 10 % | 영상 수가 다르면 프로젝트에 다른 폴더가 섞였거나 일부가 빠진 것. 실측: RedEdge 308 캡처 × 5 = 1,540 = 보고서 1,540 |
| ms.x_altitude | Flying altitude vs 비행 분석 평균 AGL | 편차 > 20 % | — | Metashape 고도는 **재구성 표면 기준** 평균이라 산림지는 수관 높이만큼 이륙점 기준 AGL 보다 낮다 (실측: 82 m vs 118 m, 편차 31 %, 수관 ~30 m). 근거 문장에 그 사실을 붙인다 |
| ms.x_gsd | Ground resolution vs 비행 분석 GSD 중앙값 | 편차 > 20 % | — | 고도와 같은 이유로 산림지는 보고서 GSD 가 더 작게 나온다 |
| ms.x_area | Coverage area vs 비행 분석 **커버리지 합집합** | 편차 > 50 % | — | 둘 다 영상 자취의 합집합이지만 Metashape 는 지상 패널 캡처·가장자리까지 포함해 더 넓다 (실측 13.0 ha vs 9.9 ha, 32 %). 추정 조사 구역(여유 0 의 궤적 외곽, 4.8 ha)은 비교 대상이 아니다 |
| ms.x_bands | 카메라 수 vs 센서 밴드 수 (RedEdge 5, M3M 4) 또는 RGB 1 | 다르면 WARN | — | 밴드 일부가 프로젝트에서 빠진 것 |

