# ASSUMPTIONS — 확정 사실과 가정의 기록

CLAUDE.md §0 규칙: 결과가 갈리는 가정은 날짜와 함께 여기에 적는다.  
구분: **[확정]** 실제 샘플/공식 문서로 확인함 · **[가정]** 아직 확인 못 함, 근거와 함께 기록 · **[결정]** 설계상 선택.

---

## 2026-09-10 (M0)

### 샘플 데이터 출처 (저장소에는 넣지 않음)

- **[확정]** RedEdge 샘플: 로컬 디스크의 2022-09-08 촬영 백업본 1 세트 (`0000SET/000/IMG_0000_1..5.tif`).
  EXIF `Model = "RedEdge-M"`, `Software = "v7.3.2"`, DLS `SwVersion = "v1.3.0"`.
- **[가정]** CLAUDE.md 는 대상을 RedEdge-**MX**, 펌웨어 **v7.5.0** 으로 명시하지만, 디스크에서 찾은 유일한 MicaSense
  원본은 RedEdge-M v7.3.2 이다 (다른 M350 현장 폴더는 전부 Zenmuse L2 LiDAR 데이터). RedEdge-M 과 MX 는 동일
  펌웨어 계열(`rededgem_*.bin`)을 쓰므로 태그 구조가 같다고 가정하고 파서를 이 샘플 기준으로 쓴다.
  → 2026-09-10 사용자 결정: **MX 는 아직 마운트가 불가해 자료가 없으므로 RedEdge-M v7.3.2 샘플 기준으로 진행한다.** MX 데이터가 생기면 `scripts/dump_tags.py` 로 문서를 재생성하고 차이를 여기에 적는다. 파서는 태그 부재 시 `None` 으로 방어적으로 처리한다.
- **[확정]** M3M 샘플: 2026-09-07 촬영 미션 폴더의 캡처 1 세트 (`*_D.JPG` + `*_MS_{G,R,RE,NIR}.TIF`).
  `Software = "11.09.10.58"`, XMP `Version = 7.0`.
- 문서화된 태그: `docs/tags_rededge_mx.json`, `docs/tags_dji_m3m.json` (사람이 읽는 값) 과 `*_numeric.json` (`-n`).
  경로·GPS 좌표·시리얼은 형식을 유지한 채 마스킹했다 (`<sample>`, `37 deg 0' 0.00" N`, `<REDACTED>`).

### 픽셀 값 / 포화 DN

- **[확정]** 두 센서 모두 `uint16`, 하위 4비트가 항상 0 이다 (12-bit ADC 값을 4비트 좌시프트).
  RedEdge-M: 폴더 `000` 의 935 개 파일 전체 최댓값 = **65520** (= 4095 × 16), 65535 는 한 번도 안 나옴.
  M3M: MS TIF 200 개 최댓값 = **65520**.
  → 포화 판정 기준 DN 은 두 센서 모두 `65520` 으로 `config/default.toml` 에 둔다.
- **[확정]** RedEdge-M `EXIF:BlackLevel = 4800` (×4 반복), 실측 최소 DN ≈ 4288–5552. M3M `XMP:BlackLevel = 3200`, 실측 최소 ≈ 3300–3700.
- **[확정]** 해상도: RedEdge-M 1280×960, M3M MS 2592×1944, M3M RGB JPG 5280×3956.

### 시간

- **[확정]** RedEdge `EXIF:DateTimeOriginal` 은 **UTC** (타임존 태그 없음). 근거: 샘플 01:32 UTC = 10:32 KST 에서
  `XMP:SolarElevation = 0.845 rad ≈ 48.4°` 로 9월 초 오전 10시 반의 한반도 태양고도와 일치. `EXIF:SubSecTime` 은 9자리 (ns 단위 소수부).
- **[확정]** M3M `EXIF:DateTimeOriginal` 은 **로컬 시각(KST)** 이고, `XMP:UTCAtExposure` 가 µs 정밀 UTC, `EXIF:GPSTimeStamp/GPSDateStamp` 도 UTC.
  같은 캡처의 RGB(`_D.JPG`) 와 MS TIF 사이 UTCAtExposure 차이 ≈ 26 ms.
- **[확정]** 샘플 파일 mtime 은 EXIF 시각과 같은 숫자 (RedEdge 는 UTC 숫자를 FAT 시각에 그대로 씀 → 로컬로 해석하면 +9h 어긋나 보임).
  파일시각-EXIF 불일치 판정(§4.2)은 이 센서별 관례를 반영해야 한다.

### 자세·태양·조도 태그의 단위

- **[확정]** RedEdge `XMP:Yaw/Pitch/Roll`, `XMP:SolarElevation/SolarAzimuth` 는 **라디안**. `IrradianceYaw/Pitch/Roll` 은 **도(°)**.
  `XMP:Irradiance`, `SpectralIrradiance`, `HorizontalIrradiance`, `DirectIrradiance`, `ScatteredIrradiance` 는 W/m²/nm (MicaSense 문서 기준, 값 크기로도 일치).
- **[확정]** M3M `XMP:GimbalPitchDegree` 등은 도(°). 직하 촬영 시 `GimbalPitchDegree = -89.9`. `XMP:Irradiance`/`SunSensor` 는 단위 없는 센서 카운트(11486 등),
  `IrradianceExposureTime`·`IrradianceGain`·`SunSensorYaw/Pitch/Roll` 동반. 밴드별 `BandFreq = "560(+/-16)nm"` 문자열, `CentralWavelength`/`WavelengthFWHM` 수치도 있음.
- **[가정]** M3M Irradiance 카운트를 밴드 간·시간 간 비교할 때 `IrradianceExposureTime × IrradianceGain` 으로 정규화해야 한다 (DJI 공개 문서 부재, 값 구조로 추정). M2 조도 안정성 분석에서 검증.

### GPS / RTK

- **[확정]** RedEdge (Quick Mount): 카메라 자체 GPS. `XMP:GPSXYAccuracy ≈ 1.06 m`, `GPSZAccuracy ≈ 1.97 m`, `EXIF:GPSDOP = 0`. RTK 관련 태그 없음. → "RTK 미고정" 판정 항목 자체를 N/A 로 두고 보고서에 명시 (CLAUDE.md §2.1).
- **[확정]** M3M: `XMP:GpsStatus = "RTK"`, `XMP:RtkFlag = 50`, `RtkStdLon/Lat/Hgt` (m, 샘플 ≈ 0.01–0.02), `RtkDiffAge`(s), `AltitudeType = "RtkAlt"`, `RelativeAltitude`(이륙점 기준 m), `AbsoluteAltitude`.
- **[가정]** `RtkFlag = 50` 이 "Fix" 이다 (DJI 문서상 50 = RTK fixed, 34 = float, 16 = single 으로 알려져 있으나 공식 표 미확인). M1 에서 한 미션 전체의 RtkFlag 분포로 교차 확인.
- **[가정]** RedEdge `EXIF:GPSAltitude` 는 "Above Sea Level" 표기이나 실제로는 GPS 타원체고일 가능성이 있다. `XMP:PressureAlt` 와 함께 기록하되 AGL 계산은 이륙점 기준 상대고도로 하므로 결과에 영향 없음.

### 폴더 구조

- **[확정]** RedEdge: `0000SET/000/`, `001/` … 하위 폴더에 `IMG_XXXX_B.tif`. 같은 폴더에 `diag.dat`, `gpslog.dat`, `hostlog.dat`, `paramlog.dat` 가 있고
  **`IM1ABD~1.TIF` 같은 8.3 형식 이름의 TIFF 도 섞여 있다** (exiftool 로 읽으면 정상 RedEdge 캡처). → discovery 는 파일명 패턴만 믿지 말고 TIFF 이면 태그로 캡처 ID 를 확인해 편입하고, 편입 사실을 로그에 남긴다.
- **[확정]** M3M: 미션 폴더 `DJI_YYYYMMDDHHMM_NNN_<사용자 미션명>/` (미션명에 **한글** 포함) 아래 `DJI_YYYYMMDDHHMMSS_NNNN_D.JPG` + `_MS_{G,R,RE,NIR}.TIF`, 그리고 `*_Timestamp.MRK`, `*_PPKRAW.bin/.nav/.obs`.
  캡처 묶음 키는 `XMP:CaptureUUID` (JPG 와 4 TIF 가 동일 값) 이며 파일명 번호는 보조.
- **[확정]** exiftool 에 비-ASCII 경로를 Windows argv 로 직접 넘기면 실패한다 (Perl 이 ANSI 코드페이지로 수신). UTF-8 argfile(`-@`) + `-charset filename=utf8` 로 넘겨야 한다. M1 의 `-stay_open` 배치도 같은 규칙 적용.

### 도구 체인 / 환경

- **[결정]** 패키지·가상환경 관리는 `uv`. Python 은 3.12 를 기본(`.python-version`)으로 하고 `>=3.11,<3.14` 지원.
- **[결정]** exiftool 13.59 Windows 64-bit 를 SourceForge 배포본에서 받아 `tools/exiftool/` 에 둔다 (git 제외, sha256 고정, `scripts/fetch_exiftool.py`).
  exiftool.org 직링크는 404 라 SourceForge 미러를 쓴다.
- **[결정]** `make` 가 없는 Windows 를 위해 `scripts/check.py` 가 `make check` 와 동일 동작을 한다.
- **[가정]** PDF 엔진(weasyprint vs Playwright/Chromium) 은 M3 에서 exe 패키징 안정성을 검증한 뒤 결정. weasyprint 는 Windows 에서 GTK/Pango 런타임이 필요해 불리할 수 있음.
- **[결정]** `pytest` 기본 실행에 커버리지 포함. exiftool 이 없으면 관련 테스트는 **skip 으로 표시**된다 (조용히 통과시키지 않음).

### 이용 기준값 (아직 config 미작성 — M1 에서 `config/default.toml` 로 이관)

- **[가정]** RedEdge-MX 센서 물리값: 4.8×3.6 mm, 초점거리 5.4 mm (CLAUDE.md §2.1). 샘플 XMP 는 `PerspectiveFocalLength = 5.431 mm`, `FocalPlaneXResolution = 266.67 px/mm` → 픽셀 3.75 µm × 1280 = 4.8 mm 로 일치.
- **[확정]** M3M MS: `PerspectiveFocalLength = 4.34 mm`, `CalibratedFocalLength = 2170 px` → 픽셀 2.0 µm, 센서 5.18×3.89 mm. RGB: 12.3 mm, `CalibratedFocalLength = 3725 px`.

### 밴드 ↔ 파일 인덱스 (파일명 순서 ≠ 파장 순서)

- **[확정]** RedEdge-M 파일 접미사 순서는 `_1` Blue 475 · `_2` Green 560 · `_3` Red 668 · **`_4` NIR 842 · `_5` Red edge 717** (`XMP:BandName`, `CentralWavelength`, `RigCameraIndex 0–4`).
  CLAUDE.md §2.1 의 나열은 파장 순이므로 파일 번호로 밴드를 추정하면 NIR/RedEdge 가 뒤바뀐다. 파서는 반드시 `XMP:BandName` 으로 밴드를 정한다. BandName 문자열은 `"Red edge"` (공백 포함).
- **[확정]** M3M 파일 접미사 `_MS_G`(560) · `_MS_R`(650) · `_MS_RE`(730, BandName `"RedEdge"`) · `_MS_NIR`(860), `RigCameraIndex 1–4`; RGB `_D.JPG` 는 `RigCameraIndex 5`. 캡처 묶음 키는 `XMP:CaptureUUID`.

## 2026-09-10 (M1)

### exiftool 처리 성능 (실측, 10 코어 데스크톱, 파일 캐시 워밍 후)

- **[확정]** 단일 `exiftool` 프로세스는 RedEdge TIFF 938 개를 13.0 s 에 읽는다 (72 파일/s). `-fast2` 나 태그 그룹 제한은 도움이 안 된다 (오히려 느림).
- **[확정]** 프로세스를 늘리면 거의 선형으로 빨라진다: 2 개 144, 4 개 268, 8 개 458 파일/s. → `[extract].workers = 0` (코어 수, 최대 8) 기본.
  4 코어 노트북 추정: 15,000 파일 ≈ 56 s (목표 60 s 경계). 실측은 `tests/bench/bench_extract.py` 와 README 참고.
- **[확정]** 실제 M3M 미션 폴더(3,550 파일, 콜드 디스크)에서 발견 31 s + 추출 18 s. 두 번째 실행(워밍)은 발견 0.9 s. 콜드 I/O 는 exiftool 이 어차피 파일을 열어야 하므로 피할 수 없다.
  발견 단계는 `os.scandir` + `DirEntry.stat()` 으로 바꿔 stat 비용을 제거했다 (0.37 s → 0.01 s).
- **[결정]** exiftool 은 `-G1 -n -struct -a` 로 읽는다. 문서(`docs/tags_*.json`)도 `-G1` 로 재생성했다 (그룹명이 네임스페이스를 드러내 파서가 명확해짐: `XMP-DLS:Yaw`, `XMP-drone-dji:RtkFlag`).
- **[결정]** 메모리 상한을 위해 원본 태그 중 필요한 그룹(`XMP-*`, `ExifIFD`, `GPS`, `IFD0`, `Composite:GPS*`, `System`, `File`)만 남긴다 (`metadata.slim_raw`). 캐시에도 그 축약본을 zlib 압축해 저장한다.
- **[확정]** 캐시 키 = 경로+크기+mtime 해시. exiftool 버전이 다르면 미스. 실패 파일은 캐시하지 않는다.

### Windows 파일명·exiftool 쓰기 (합성 데이터 생성기)

- **[확정]** NTFS 에서 `IMA471~1.TIF` 처럼 8.3 단축명 형식의 이름으로 파일을 새로 만들면 다른 파일의 자동 단축명과 충돌해 rename 이 실패한다. 합성 데이터의 "규칙 불일치 파일" 은 `IMxxx_X.TIF` 로 흉내 낸다. 실제 카드에서 온 `IM1ABD~1.TIF` 는 읽기만 하므로 문제없다.
- **[확정]** MicaSense(`XMP-Camera`, `XMP-DLS`, `XMP-MicaSense`)·DJI(`XMP-drone-dji` 의 BandName/CaptureUUID 등) 태그는 exiftool 기본 정의에 쓰기 가능으로 없다. `tests/fixtures/exiftool_synth.config` 에 네임스페이스 URI 를 실제 파일에서 읽어 정의했다:
  `Camera=http://pix4d.com/camera/1.0`, `DLS=http://micasense.com/DLS/1.0/`, `MicaSense=http://micasense.com/MicaSense/1.0/`, `drone-dji=http://www.dji.com/drone-dji/1.0/`.
- **[확정]** argfile 로 여러 명령을 줄 때 마지막 명령 뒤에 `-execute` 를 붙이면 빈 명령이 되어 "No file specified" 로 rc=1 이 난다.
- **[결정]** M3M RGB JPG 합성은 PySide6 `QImage.save` 로 만든다 (화면 없이 동작 확인).

### 실제 데이터로 확인한 사실

- **[확정]** 2022 RedEdge 백업 폴더(`0000SET/000`) 는 비행이 아니라 지상 정지 테스트다: 189 캡처, 1 s 간격, 6 분, 이동 34 m. 파이프라인은 이를 FAIL(고도·속도·직하) 로 판정했고 8.3 이름 파일 3 개 중 2 개를 태그로 편입, 1 개는 중복으로 제외했다. 정상 동작.
- **[확정]** 실제 M3M 미션(710 캡처, 20 라인, 간격 2.0 s, 라인 간격 9.5 m, 상대고도 36–60 m 로 지형 따라 상승): RtkFlag 50 이 558, **34 가 152 (21 %)**. 34 = Float 로 보이며 Fix 비율 78.6 % 는 FAIL 게이트. 이 비행은 실제로 절대정확도 검토가 필요하다.
- **[확정]** DJI 는 짐벌 요가 180° 뒤집힌 캡처(라인 방향 반전)에서 `GimbalRollDegree = +180.00`, `GimbalPitchDegree = -90` 으로 기록한다 (710 캡처 중 335). 직하 촬영이므로 롤을 ±90° 범위로 접어서 기울기를 계산한다. 접지 않으면 절반이 180° 이탈로 오판된다.
- **[확정]** UTM 투영 방위각은 격자북 기준이라 진북 대비 자오선 수렴각(zone 52, 경도 127°E 에서 ≈ 1.2°) 만큼 어긋난다. 라인 방향은 상대값이므로 영향 없음.
- **[결정]** RedEdge 이륙점 자동 추정: 첫 캡처 고도. 첫 캡처가 이미 비행고도(중앙 고도 − 첫 캡처 < 목표 AGL 의 25 %)면 AGL 항목을 N/A + 경고로 둔다. 근거는 docs/QA_RULES.md.
- **[결정]** 라인 분리: 진행방향 45° 이상 꺾임 또는 중앙 간격 3 배 이상 공백에서 새 라인. 꺾임 직전 캡처는 새 라인의 시작점으로 편입한다(구간 i-1→i 가 새 방향이므로). 정지 상태(이동 < 0.5 m)는 방향 없음.

## 2026-09-10 (M2)

- **[확정]** pvlib 겉보기 태양고도가 RedEdge DLS 의 `XMP-DLS:SolarElevation` 과 실제 샘플 189 캡처에서 중앙 차 0.00° 로 일치한다. DLS 값이 라디안이라는 해석과 시각이 UTC 라는 해석이 동시에 검증됐다.
- **[확정]** 픽셀 통계 처리량 (10 코어, 파일 캐시 워밍): RedEdge 2.4 MB TIFF 937 개를 stride 1 에서 6.6 s (142 파일/s), stride 4 에서 3.9 s (243 파일/s). 15,000 파일 추정: stride 1 ≈ 106 s, 메타데이터 20–36 s 와 합쳐 목표 4 분 이내 (추정, 콜드 디스크는 별도).
- **[결정]** 지상 캡처(패널 후보)에서만 밝은 영역 힌트를 계산한다(픽셀 통계 2 회 읽기 없이 한 번에). 지상 판정은 AGL ≤ 5 m, AGL 을 모르면 최저 고도 +5 m 이내.
- **[결정]** 패널 필수 여부는 센서별 설정(`[panel].required_for`, 기본 RedEdge 만). M3M 은 DJI Terra 가 조도 센서로 반사율을 보정하고 패널은 선택 워크플로우이므로 없어도 N/A. 근거: DJI Terra 다중분광 처리 문서(패널 사용은 선택), 사용자 확인 필요 시 변경 가능 **[가정]**.
- **[결정]** 커버리지 사각형은 이미지 가로(width) 축이 진행방향에 수직이라고 본다. RedEdge 는 짐벌 없이 기체 전방 = 이미지 상단으로 장착하는 것이 표준 (MicaSense 장착 가이드), M3M 은 짐벌 요가 비행 방향을 따른다. 90° 돌려 장착했다면 종/횡 중첩이 뒤바뀐다 **[가정]** — 실제 정사영상 결과와 대조 필요.
- **[결정]** 판정 경계값: "이상" 조건(75 % 등)은 값이 경계와 같으면 통과 (`grade_min` 은 `<`), "이하/미만" 조건은 `grade_max` 가 `≥` 로 WARN. 문서의 부등호와 코드가 같다.
- **[확정]** `python - <<EOF` 처럼 stdin 스크립트로 파이프라인을 부르면 Windows spawn 이 `__main__` 을 다시 읽지 못해 프로세스 풀이 죽는다. 풀 실패 시 순차 처리로 이어가는 안전장치를 넣었고(BrokenProcessPool), CLI·pytest·얼린 exe 에서는 발생하지 않는다.
- **[가정]** 합성 데이터의 조도 급락(0.5 배) 이 실제 구름 통과와 비슷한 크기라는 가정. 실측 구름 이벤트 데이터가 생기면 `drop_factor` 를 재검토.

## 2026-09-10 (M3)

- **[결정]** PDF 엔진은 PySide6 **QtWebEngine** (Chromium `printToPdf`). Plotly 차트가 JS 로 그려지므로 JS 실행이 필요해 weasyprint 는 제외했고, Playwright 는 Chromium(≈150 MB) 별도 다운로드가 필요해 오프라인 exe 배포에 불리하다. QtWebEngine 은 GUI 용 PySide6 에 이미 포함되고, 화면 없이(`QT_QPA_PLATFORM=offscreen`, `--disable-gpu`) 한글·차트를 정상 렌더하는 것을 실측했다 (PDF 0.5 MB, 폰트 임베드 확인).
- **[확정]** plotly.js 인라인은 4.85 MB 라 HTML 보고서가 4.9 MB 다. 메일 첨부 가능 크기로 보고 단일 파일 원칙(§5.1)을 지켰다. 필요하면 M4 이후 경량 SVG 렌더러로 대체 가능.
- **[결정]** 다국어 범위: 섹션·표 머리글·항목 라벨·상태는 ko/en 리소스로 번역하지만, 분석 모듈이 만드는 **근거 문장과 다음 행동은 한국어로 생성** 된다. 영어 보고서에는 그 사실을 명시한다. 완전한 문장 번역은 분석 모듈의 문자열을 키 기반으로 재구성해야 하므로 후속 과제로 둔다.
- **[확정]** 레이아웃 실측(QtWebEngine): 1280/800/400 px 폭에서 가로 오버플로 요소 0 개, 차트 7 개 렌더. 테스트로 고정.
- **[결정]** 커버리지 사각형은 캡처별 진입 구간 방향이 아니라 **조사 라인 방향** 으로 놓는다. 라인 첫 캡처의 진입 구간은 전환 방향이라 사각형이 비틀리기 때문.
- **[확정]** 워커 프로세스(spawn)는 `__main__` 모듈을 다시 실행하므로, 파이프라인을 부르는 스크립트는 반드시 `if __name__ == "__main__":` 가드가 있어야 한다. 없으면 자식이 데이터 생성을 반복해 파일이 겹쳐 쓰인다(스모크에서 실제 발생). CLI·GUI 진입점은 가드가 있다.
- **[확정]** QtWebEngine 을 `QT_QPA_PLATFORM=offscreen` 으로 돌리면 렌더는 정상이지만 프로세스 종료 시 접근 위반(0xC0000005, exit 139)이 난다 (플래그 조합·single-process·소프트웨어 GL 모두 동일, 실측). 네이티브 windows 플랫폼 + `WA_DontShowOnScreen` 창은 화면에 아무것도 띄우지 않고 종료 코드 0 이다. → Windows 에서는 네이티브 플랫폼을 쓰고, 디스플레이가 없는 Linux/macOS 에서만 offscreen 으로 간다. pytest 도 같은 경로를 쓴다 (`tests/test_report.py`).

## 2026-09-10 (M4)

- **[결정]** GUI 는 PySide6 위젯 + 디자인 토큰에서 생성한 QSS (`gui/theme/qss.py`). 모든 위젯 상태(hover/pressed/disabled/focus)를 정의해 기본 Qt 스타일이 드러나지 않게 했다. 아이콘은 직접 그린 24×24 선형 SVG (라이선스 부담 없음), 토큰 색으로 렌더.
- **[결정]** 결과 화면의 지도·차트는 M3 에서 만든 `report.html` 을 QWebEngineView 로 그대로 띄운다. 같은 시각 자료를 두 벌 만들지 않고, 보고서와 앱이 같은 모습을 보장한다. `FLIGHTQA_NO_WEBVIEW=1` 이면 웹뷰를 끈다 (테스트·offscreen).
- **[결정]** 분석은 QThread 워커에서 `pipeline.run` 을 그대로 돌리고(GUI·CLI 동일 진입점), 취소는 `CancelToken`. 실측: 추출 단계에서 취소 신호 후 1 초 이내에 워커 종료 (테스트 `test_cancel_within_one_second`).
- **[확정]** offscreen 플랫폼에서는 시스템 글꼴이 없어 글자가 네모로 렌더된다. 스크린샷·GUI 테스트는 Windows 네이티브 플랫폼 + `WA_DontShowOnScreen` 창으로 찍는다 (실제 글꼴, 화면에 창 없음, 종료 코드 0).
- **[확정]** `QPixmap`/`QIcon` 은 QApplication 이 없으면 프로세스가 즉시 종료된다 (테스트에서 재현). 아이콘 렌더는 앱 생성 뒤에만 호출한다.
- **[결정]** 설정 화면의 임계값 필드는 `default.toml` 의 주석을 파싱해 설명·툴팁으로 보여준다. 문서와 UI 가 같은 문장을 쓴다. 프리셋은 `%LOCALAPPDATA%/flightqa/presets/*.toml` 에 저장하며 default.toml 위에 덮어쓰는 차분만 담는다.
- **[결정]** 결과 위치 기본은 `<원본 폴더>/flightqa_out`. 원본 폴더에 쓰기 싫은 경우 설정에서 상위 폴더를 지정한다 (테스트·스크린샷은 지정 폴더 사용).

## 2026-09-10 (M5)

- **[결정]** PyInstaller **onedir** (`packaging/flightqa.spec`): GUI `FlightQA.exe` 와 CLI `flightqa-cli.exe` 가 `_internal` 을 공유. onefile 은 시작 지연·오탐 때문에 제외. exiftool 은 `_internal/tools/exiftool/` 에 Perl 런타임째 번들되고 `core/exiftool.py` 가 `sys._MEIPASS` 아래를 먼저 찾는다 (실측: 빌드본 `version` 이 번들 경로를 출력).
- **[확정]** Windows 는 파일명 대소문자를 구분하지 않아 `FlightQA.exe` 와 `flightqa.exe` 가 같은 파일로 겹쳐 GUI 가 CLI 로 덮여 쓰였다(첫 빌드에서 실제 발생). CLI 이름을 `flightqa-cli.exe` 로 바꿨다.
- **[확정]** 배포 크기: zip 약 342 MB, 풀면 약 830 MB. QtWebEngine(Chromium) 이 대부분이다. PDF·결과 화면 지도가 이에 의존하므로 감수한다. 불필요한 Qt 모듈(3D·Quick3D·Charts·Multimedia 등)은 spec 의 `excludes` 로 뺐다.
- **[결정]** 설치 프로그램(Inno Setup)은 선택: `ISCC.exe` 가 PATH 에 있을 때만 `build_windows.ps1` 이 `FlightQA-<ver>-setup.exe` 를 만든다. 이 PC 에는 Inno Setup 이 없어 zip 배포본만 검증했다.
- **[결정]** 서명 없는 exe 가 차단되는 기관 PC 를 위해 `run.bat`(uv 로 소스 실행) 을 동봉한다.
- **[가정]** "Python 없는 Windows VM 에서 exe 실행" 은 이 PC(Python 설치됨)에서 빌드본을 별도 프로세스로 실행해 검증했다. PyInstaller 번들은 시스템 Python 을 쓰지 않지만, 완전한 검증은 사용자가 다른 PC 에서 zip 을 풀어 실행해 확인해야 한다.
- **[확정]** 빌드 정보(git 해시·빌드일)는 `packaging/write_build_info.py` 가 `src/flightqa/_build.py` 로 생성(커밋 제외)하고 보고서 부록·앱 정보에 표시된다.
- **[확정]** 빌드본 검증 (2026-09-10, 같은 PC, 별도 프로세스): `flightqa-cli.exe version` 이 번들 exiftool 13.59 를 찾고, 합성 폴더 `run --pdf --json` 이 종료 코드 1(WARN)·JSON·HTML·PDF·CSV·GeoJSON 을 모두 만들었다 (픽셀 워커 spawn 포함, 2.1 s). `FlightQA.exe` 는 창 제목 "FlightQA 0.1.0.dev0" 으로 떠 있었다. 첫 빌드에서 얼린 exe 의 콘솔이 cp949 라 `--json` 출력이 UnicodeEncodeError 로 죽어, CLI 시작 시 stdout/stderr 를 UTF-8 로 재설정했다.

## 2026-09-11 (보고서 개선 · 지형추종)

- **[확정]** 실제 M3M 미션(2026-09-07 촬영, 710 캡처, 20 라인) 재분석: 이륙점 기준 상대고도가 라인 안에서 10–25 m 오르내리고, 왕복하는 인접 라인의 고도 프로파일 상관이 19 쌍 중 18 쌍에서 r = 0.93–1.00, `AbsoluteAltitude − RelativeAltitude` 의 표준편차 0.01 m (이륙점 기준 상수). 사용자 확인: **DSM 추종으로 비행**했고 DSM 은 DJI 서버에서 받아 파일이 없다. → 데이터만으로 감지하는 규칙(QA_RULES "지형추종 감지")을 넣었고, 이 미션은 변화폭 17 m, r = 0.99 로 감지되어 AGL 세 항목이 N/A 가 되고 게이트 실패는 RTK 하나만 남는다.
- **[확정]** 같은 미션의 RTK Float 152 캡처(21 %)는 네트워크 RTK 보정이 중간에 끊긴 것(사용자 확인). 폴더에 PPK 원시파일(`_Timestamp.MRK`, `PPKOBS.obs`, `PPKRAW.bin`, `PPKNAV.nav`)이 있어 DJI Terra PPK 후처리로 복구 가능하므로 판정 문구에 힌트를 붙인다.
- **[가정]** DEM 기준 실제 AGL 은 합성 GeoTIFF(UTM 52N, 5 m 격자, 사인 지형)로만 검증했다. DJI `AbsoluteAltitude`(`AltitudeType = RtkAlt`)가 타원체고라는 전제로 `geoid_offset_m` 을 두었고 기본값 0 이다. 국내 DEM(정표고)과 함께 쓰려면 사용자가 지오이드고(한반도 중부 약 +25 m)를 넣어야 한다. 실제 DEM 으로 검증되면 여기에 기록한다.
- **[확정]** 표 머리글 `position: sticky; top: 44px` 가 `overflow-x: auto` 컨테이너 안에서 컨테이너 기준으로 44 px 내려앉아 빈 띠가 생기고 첫 그룹 행("캡처 무결성")을 가렸다 (스크린샷으로 확인). sticky 를 제거했다.
- **[확정]** Plotly 가로 범례는 항목을 왼쪽부터 채우고 넘치면 접어 "3+1" 이 된다. `entrywidthmode: fraction` 은 항목 폭을 **그래프 영역 폭의 비율**로 고정하므로 1/열수 로 주면 대칭 격자가 된다 (실험: 500 px 에서 0.5 → 2×2, 320 px 에서 0.25 → 겹침). 글자 폭은 캔버스 `measureText` 가 SVG 실측보다 20 % 작게 나와, 그린 뒤 `.legend text` 의 `getBBox()` 로 재서 열 수를 정한다. 빈 데이터 트레이스(조도 급락 이벤트 0 개)는 범례에서 자동으로 빠지므로 실측 기준으로 세어야 맞는다.
- **[확정]** 지도 범례가 세로로 쌓이던 원인은 커버리지 트레이스의 `legendgroup` 이었다 (그룹 경계에서 줄이 바뀜). 제거했다. 궤적 선은 범례에서 뺐다 (2 항목만 남겨 인쇄 폭에서도 한 줄).
- **[결정]** 인쇄·PDF 는 `beforeprint` 에 의존하지 않는다. QtWebEngine `printToPdf` 는 이 이벤트를 신뢰성 있게 내지 않아 GUI PDF 와 브라우저 인쇄 결과가 달랐다. 대신 로드 직후 `Plotly.toImage` 로 A4 폭(703 px 기준, 열 수에 따라 분할)에 맞춘 PNG(3 배)를 만들어 두고 `window.__flightqa_print_ready` 로 알린다. PDF 엔진은 이 신호를 기다린다 (실측: 8 차트 약 5 s, PDF 1.8 MB, 9 쪽). 전체 페이지 스크린샷도 같은 신호를 기다린다 — 이미지 생성 중 캡처하면 뷰포트 리사이즈 직후라 아래가 비어 있었다.
- **[확정]** 시계열을 인쇄에서 2 열로 두면 칸 폭 319 px 에서 범례가 4 줄로 쌓이고 축 제목이 겹친다 (스크린샷 확인). 인쇄에서는 시계열 1 열(전폭 677 px), 지도 2 열, 카드 5 열로 둔다.
- **[확정]** 닫힌 `<details>` 의 내용은 CSS 로 인쇄에 드러낼 수 없다 (Chromium 은 닫힌 슬롯을 렌더하지 않음). 설정 JSON 은 인쇄 전용 복제 블록(2 단 9 px, 1 쪽)으로 넣고, 원본 태그 샘플은 인쇄에서 뺀다.
- **[결정]** 위성 배경지도: CLAUDE.md §5.1 의 "설정에서 온라인 타일 허용 시 folium 지도로 교체" 를 folium 대신 **같은 Plotly 지도에 슬리피 타일을 행 단위로 합쳐 `layout.images` 로 까는 방식**으로 구현했다 (추가 의존성 없음, 단일 파일 유지, 인쇄 이미지에도 그대로 들어감). 기본 제공자 Esri World Imagery(키 불필요, 출처 표기 내장, `crossOrigin=anonymous` 로 캔버스 합성 가능), 브이월드는 API 키 필요, `custom` 은 XYZ 템플릿. 구글 위성은 약관상 제외. 기본 꺼짐(오프라인 원칙)이고 `online_basemap = true` 면 자동. 타일이 안 오면(오프라인) 상태 문구만 보이고 지도는 그대로다. 위경도 축에 타일을 선형으로 늘려 넣는 근사는 조사지 수 km 규모에서 수십 cm 이하 오차다.
- **[가정]** 브이월드 타일 서버의 CORS 허용 여부는 확인하지 못했다. 캔버스가 오염되면 `toDataURL` 이 실패해 배경이 실리지 않고 상태 문구가 남는다.
- **[확정]** 두 지도의 확대 범위 연동은 UTM 상대좌표 ↔ 위경도의 최소제곱 아핀 근사(`layout.meta.affine`)로 한다. 조사지 규모에서 오차 m 미만.
- **[확정]** GUI 결과 화면의 웹뷰에서 "위성 배경" 이 "인터넷 연결 필요" 로 실패한 원인은 QtWebEngine 기본값 `LocalContentCanAccessRemoteUrls = false` (file:// 페이지의 원격 접근 차단) 이었다. 브라우저에서는 정상. 결과 화면 웹뷰와 PDF 렌더러에서 켰다.
- **[확정]** Plotly 가로 범례는 `entrywidthmode: fraction` 으로 대칭 격자는 되지만 칸 안에서 왼쪽 정렬이라 "그래프 중앙 정렬" 이 되지 않는다. 범례를 HTML 로 직접 그려(트레이스 이름·색·대시 스와치, 클릭 토글) CSS grid 로 중앙에 두었다. 인쇄에서도 HTML 범례가 그대로 나온다.
- **[확정]** 전체 페이지 스크린샷을 뷰포트를 문서 높이(7,000 px 이상)로 늘려 한 번에 찍으면 첫 화면 아래가 비어 나온다 (그림자·블러가 있는 새 스타일에서 재현). 뷰포트 높이만큼 스크롤하며 조각을 찍어 이어붙이는 방식으로 바꿨고, 스크롤 뒤 두 프레임 + 500 ms 를 기다려야 위젯 프레임이 갱신된다 (150 ms 는 이전 조각이 다시 찍힘).
- **[확정]** 보고서 CSS 의 상태 클래스(`.fail` 등)는 글자색과 배경을 함께 바꾸므로 판정 카드·그룹 표 같은 블록에 붙이면 안의 글자가 전부 상태색이 된다 (PDF 3 쪽 실측). 블록은 `data-status` 속성으로만 꾸민다.
- **[확정]** GUI 상세 표에서 셀에 마우스를 올리면 진한 브랜드색이 글자를 가린 것은 QSS 에 `::item:hover` 가 없어 기본 스타일의 강조색이 쓰였기 때문이다. hover 브랜드색 8 %(다크 14 %), 선택 `brand_soft`, 표 `NoFocus` 로 고쳤다.
- **[확정]** 판정 배지를 `setCellWidget` 으로 넣으면 배지(QLabel)가 셀 높이만큼 늘어난다. 배지의 크기 정책을 Fixed 로 두고 레이아웃 정렬 AlignCenter 로 두면 행 높이와 무관하게 같은 크기다. 행 최소 높이 44 px.
- **[확정]** `QTableWidget` 의 근거 열은 글머리표·색을 위해 `QStyledItemDelegate` 가 `QTextDocument` 로 그린다. 행 높이는 위임자의 `sizeHint` 가 현재 열 너비로 문서를 재서 정하며, 표 너비가 바뀌면 `resizeRowsToContents` 를 다시 부른다 (`AutoHeightTable.fit`).
- **[확정]** 권고 행동·근거 문장의 줄바꿈은 `(?<=[.!?])\s+(?=\S)` 로 마침표 직후에서만 나눈다. "1.5 s", "PPKNAV.nav," 같은 약어·소수는 뒤에 공백이 없거나 쉼표가 붙어 잘리지 않는다. PPK 힌트는 앞 문장에 마침표를 붙여 새 문장으로 잇는다.
- **[가정]** GUI 웹뷰에서 시계열 그래프가 상자 밖으로 넘친 현상은 이 PC 의 재현 절차(숨겨진 웹뷰에 로드 → 탭 표시, 1920×1040)로는 재현되지 않았다. 원인 후보는 컨테이너 크기가 바뀐 뒤 Plotly 가 다시 맞춰지지 않은 것이라 `.chart` 높이 고정 + `overflow: hidden` + `ResizeObserver` + 탭 표시 시 `resize` 이벤트로 막았다. 재발하면 `scratchpad/gui_probe.py` 의 JS 측정(그림 높이 vs 차트 svg 높이)으로 확인한다.
- **[결정]** 종합 판정 헤드라인은 짧은 결론만 두고(`headline`) 근거는 `headline_detail` 로 분리했다. summary.json 스키마 1.3 (`headline_detail`, `action_items` 추가, 기존 필드는 유지). 1.2 이전 파일도 기본값으로 읽힌다.
- **[확정]** GUI 보고서 탭에서 웹뷰를 문서 높이(11,255 px)로 키우면 아래쪽 수백 px 이 비어 보인다 (실측; 앞서 7,000 px 스크린샷에서도 같았음). 웹뷰는 창 높이로 두고 휠을 바깥 스크롤과 이어 주는 방식으로 정했다.
- **[확정]** QtWebEngine 은 페이지가 처리하지 못한 휠(맨 위에서 위로)을 부모 위젯으로 넘긴다 (프로브: 바깥 스크롤 557 → 437). 아래 방향은 이벤트 필터가 바깥을 먼저 내린다.
- **[확정]** 홈 최근 목록의 "복사/전체 선택" 우클릭 메뉴는 이름·메타 QLabel 의 `TextSelectableByMouse` 가 만든 것. `NoTextInteraction` 으로 없앴다.
- **[확정]** 탭 페이지에 `Ignored` 크기 정책을 `currentChanged` 에서만 걸면 첫 탭 추가 때만 적용된다(이후 addTab 은 신호를 내지 않음). addTab 마다 다시 적용해야 숨은 페이지가 스택 높이에 끼어들지 않는다.

## 2026-09-30 (조사 구역 추정 · GIS 내보내기)

- **[확정]** 조정기(DJI Pilot 2 / RC Pro)에서 그린 임무 폴리곤·웨이포인트는 영상 EXIF/XMP 어디에도 없다. `docs/tags_dji_m3m.json` 의 XMP 태그에 임무·경로·폴리곤 관련 항목이 없고(비행 자세·속도뿐), 실제 M350 미션 폴더에는 MRK·RTK·SIG·PPK 원시 파일만, MicaSense SET 폴더에는 카메라 로그 `*.dat` 만 있다. 로컬 디스크 전체에서 KMZ·WPML 도 GDAL 테스트 데이터 외에는 없다. Pilot 2 는 임무를 조정기 안에 KMZ(WPML) 로 보관하고 비행 경로 라이브러리에서 내보내야 얻을 수 있다.
- **[결정]** 사용자 결정: KMZ 없이 **궤적 기반 추정**으로 먼저 구현. 조사 라인 캡처점(전환 구간·지상 캡처 제외)의 오목 껍질(`shapely.concave_hull`, ratio 0.3) 에 여유 폭을 더한 폴리곤을 "추정 조사 구역" 으로 쓴다 (`analysis/survey_area.py`, `SurveyArea.method = "trajectory_hull"`). 임무 파일 입력 경로가 생기면 `method` 로 구분한다.
- **[확정 → 기본값 변경]** 여유 폭 가정("라인이 폴리곤 안쪽에 놓이므로 짧은 변 × 0.5")은 틀렸다. 사용자가 Pilot 2 (M3M, RC Pro) 에서 내보낸 실제 임무 KMZ(`영역-경로-만들기19`, mapping2d, margin 10 m, 중첩 85/85 %, 고도 108 m, 웨이포인트 24 = 12 라인)와 같은 임무의 비행(2026-09-29, 197 캡처)을 겹쳐 본 결과: 조사 라인 캡처점의 오목 껍질(ratio 0.3)과 임무 폴리곤의 IoU 가 여유 0 m 에서 **0.88** 로 최대이고 9.7 m 0.81, 19.4 m 0.69, 48.6 m(0.5 × 짧은 변) 0.46 으로 단조 감소. 껍질 경계는 횡방향으로 폴리곤보다 약 9 m **밖**, 종방향(라인 끝)으로 약 5 m **안** 이었다 — Pilot 2 는 바깥 라인을 폴리곤 경계 위에 놓고 라인 끝을 margin 만큼 늘린다. → `margin_frac` 기본값을 0.5 에서 **0** 으로 바꿨다. 표본이 1 건이므로 M350 + Pilot 2 (RC Plus) 임무가 생기면 다시 확인한다.
- **[확정]** 같은 임무의 KMZ 를 `--mission` 으로 주면 임무 폴리곤 4.15 ha, 충족률 100 %, 폴리곤 밖 캡처 31/196 (15.8 %, 12 라인 양끝 + margin 10 m 구간). 궤적 추정(옛 기본 0.5)은 9.08 ha 로 2.2 배 과대였고, 새 기본(0)은 껍질 4.6 ha 수준으로 폴리곤에 근접한다.
- **[확정]** KMZ 구조: `wpmz/template.kml` 에 `wpml:templateType = mapping2d`, `Placemark/Polygon` (조정기에서 그린 구역, `lon,lat` 순서, 닫히지 않음), `wpml:margin`, `wpml:orthoCameraOverlapH/W`, `wpml:height`, `wpml:direction`, `wpml:autoFlightSpeed`, `wpml:elevationOptimizeEnable`; `wpmz/waylines.wpml` 에 웨이포인트 `Placemark/Point` 와 `wpml:distance/duration`. `takeOffRefPoint` 만 `lat,lon,alt` 순서. 전체는 `docs/tags_dji_wpml.md` (좌표 마스킹). 네임스페이스는 `http://www.dji.com/wpmz/1.0.6` 이고 파서는 실제 값을 읽어 다른 버전도 시도한다.
- **[결정]** 임무 파일은 **선택 입력** 이다: `survey_area.mission_path` (CLI `--mission`) → 데이터 폴더 깊이 3 안의 `*.kmz`/`*.kml` 자동 탐색(폴더명 `DJI_…_<임무명>` 과 같은 이름 우선, 여러 개면 첫 파일 + 경고) → 없으면 궤적 추정. 읽기 실패는 경고로 남기고 추정으로 간다 (조용히 건너뛰지 않음). 사용자 확인: 매번 KMZ 를 내보낼 수는 없으므로 평소에는 추정으로 쓰고 KMZ 는 필요할 때만.
- **[확정]** 실측: RedEdge-MX 2026-09-29 현장 A 세트(308 캡처, 18 라인, AGL 118 m) 추정 구역 8.76 ha, 충족률 100 %, 미촬영 0. M3M 2026-09-07 현장 B 미션(710 캡처, 20 라인, DSM 추종) 추정 구역 6.28 ha, 여유 22 m, 충족률 98.2 %, 미촬영 조각 1개 1,128 m². 추정·내보내기 시간은 분석 0.4 s 안에 든다 (`timings_s.analyze`).
- **[결정]** GIS 내보내기는 GDAL/rasterio 없이 쓴다 (`report/geo.py`): GeoPackage 1.3.1 은 sqlite3 + shapely WKB(`GP` 헤더, XY envelope, `application_id = GPKG`, `user_version = 10301`), KML 은 xml.etree, GeoTIFF 는 tifffile 의 extratags(ModelPixelScale·ModelTiepoint·GeoKeyDirectory·GDAL_NODATA) 로 `analysis/dem.py` 가 읽는 규약과 같다. exe 크기 변화 없음.
- **[확정]** QGIS 4.0.1 동봉 GDAL 3.12.2 의 `ogrinfo`/`gdalinfo` 로 검증: `flightqa.gpkg` 6 레이어 모두 WGS 84 로 열리고, `flightqa.kml` 은 LIBKML 드라이버로, `overlap_count.tif` 는 `WGS 84 / UTM zone 52N`, 픽셀 2 m, NoData 0, 최대 57 겹(현장 A 세트, 라인 간격 14.8 m) 으로 읽힌다.
- **[결정]** 래스터는 비행이 여러 개여도 한 파일에 합친다 (첫 비행의 UTM 존 기준, 다른 존은 위경도에서 재투영). 셀 2 m, 픽셀 1,600 만 초과 시 셀을 키운다. `overlap_count.tif` 의 0 은 NoData(덮지 않음) 이라 QGIS 에서 투명하게 보인다.
- **[확정]** tifffile 2026.9.9 스텁은 `extratags` 를 5-튜플 `(code, dtype, count, value, writeonce)` 로 요구한다. `tests/test_terrain_dem.py` 의 4-튜플이 `mypy --strict` 에서 실패하고 있었고(이번 변경과 무관), 5-튜플로 고쳤다.
- **[결정]** summary.json 스키마 1.4: `flights[].survey_area` 추가 (없으면 null). 1.3 이전 파일도 기본값으로 읽힌다.

## 2026-09-30 (폴더 구성 정리)

- **[결정]** 배포 폴더는 사용자 눈에 `FlightQA.exe`, `시작하기.txt`, `docs\`, `tools\flightqa-cli.cmd` 만 보이게 한다. PyInstaller onedir 은 exe 와 `_internal\` 이 같은 폴더에 있어야 하고 CLI exe 도 같은 `_internal` 을 쓰므로 옮기지 못한다 → `_internal`, `flightqa-cli.exe` 에 Windows 숨김 속성만 준다 (실행 무관). `run.bat` 은 exe 옆에서는 exe 를 켤 뿐이라 배포본에서 뺐다 (소스 트리용으로 packaging 에는 남김). zip 은 숨김 속성을 보존하지 않지만 실행에는 영향이 없다.
- **[결정]** 저장소 생성물은 `_out\` 하나로 모은다: `_out\dist` (PyInstaller 출력·zip·설치 파일), `_outbuild` (workpath), `_out\cache\{ruff,mypy,pytest,.coverage}` (pyproject 의 cache 경로). 빌드 스크립트가 `_out` 과 `.venv` 에 숨김 속성을 준다. `.git` 은 git 이 만들 때부터 숨김이다. 옛 `dist\`, `build\`, 점 캐시 폴더는 삭제했고 .gitignore 에는 안전을 위해 남겨 뒀다.
- **[확정]** 첫 정리 시 `dist\FlightQA` 삭제가 실패한 원인은 사용자가 그 exe 를 실행 중이었기 때문. 빌드 스크립트는 이제 배포 폴더 안의 exe 프로세스를 먼저 찾아 실행 중이면 안내 문구와 함께 멈춘다 (반쯤 지워진 폴더를 만들지 않음).
- **[확정]** Windows PowerShell 5.1 은 BOM 없는 UTF-8 스크립트를 ANSI 로 읽어 한글 경로 문자열이 깨진다 (실측: `Get-Content packaging/시작하기.txt -Raw` 가 "매개 변수 Raw 없음" 오류로 변형). `build_windows.ps1` 은 UTF-8 BOM 으로 저장한다.
- **[확정]** `Compress-Archive` 는 숨김 속성 항목을 건너뛴다 (실측: `_internal` 이 빠져 zip 이 25 MB). 숨김 속성은 zip·설치 파일을 만든 뒤 마지막에 주고, zip 크기가 200 MB 미만이면 빌드를 실패로 처리한다.
- **[확정]** cmd.exe 는 배치 파일을 ANSI 코드페이지로 읽으므로 UTF-8 한글 주석이 든 `.cmd` 는 오동작한다 (실측: `rem` 줄의 괄호가 명령으로 실행됨). `tools\flightqa-cli.cmd` 는 ASCII 만 쓴다.
- **[확정]** 모래시계 스피너를 16 ms 마다 다시 그리면 전체 테스트 부하에서 취소 응답이 1.3 s 로 늘어 기준(1 s)을 넘겼다. 30 fps + 정지 구간 미갱신으로 바꾼 뒤 통과.

## 2026-09-30 (비행별 임무 매칭 · 임무 경로 화면)

- **[확정]** 강릉 날짜 폴더(2026-09-29, 하위 10개 = 임무 3 + 패널 폴더 7, 캡처 999) 에 사용자가 KMZ 3개(`비행경로_강릉_01/02/03.kmz`) 를 두자 첫 구현은 첫 파일 하나를 세 비행 모두에 적용했다 (9.17 ha 가 세 번). 각 폴리곤이 덮는 캡처 비율은 01→비행1 87 %, 02→비행2 74 %, 03→비행3 85 %, 나머지 0 % 로 대응이 명확하다. → `match_mission`: 비행마다 조사 라인 캡처 중 폴리곤 안 비율이 `mission_match_min_pct`(50 %) 이상인 파일 중 최대를 고르고, 5 %p 이내로 비슷하면 폴더명 임무명 일치 → 생성 시각이 비행 시작에 가까운 파일. 좌표가 없으면 이름 일치 → 하나뿐이면 그것. 재실행 결과 비행 1·2·3 이 각각 01·02·03 (9.17 / 6.42 / 4.15 ha, 충족 100 %, 폴리곤 밖 11.5 / 24.6 / 15.8 %) 로 붙었고 경고 없음. `RunReport.missions` 에 실제로 쓰인 파일만 담고 GIS 레이어도 그것만 낸다.
- **[확정]** 같은 날짜 폴더에서 패널 폴더(캡처 2–3장, 임무 앞뒤 2–4분) 는 10분 공백 규칙으로 임무와 같은 비행에 묶여 '비행 전/후 패널' 로 PASS 판정된다. 임무 폴더만 따로 놓으면 패널은 보이지 않으므로 README 에 "날짜 폴더를 통째로" 를 안내한다.
- **[확정]** `QMessageBox` 가 Windows 다크 모드(OS) + 앱 라이트 테마에서 검은 배경·회색 글자로 떴다 (스크린샷). Qt 의 네이티브 대화상자 팔레트가 앱 QSS 와 따로 놀기 때문. 디자인 시스템으로 그린 `ConfirmDialog`(objectName root, 자기 배경을 토큰으로 직접 지정, 아이콘 세트 사용) 로 대체했다. 홈의 다시 분석·제거·비우기, 결과 열기 실패 알림이 대상.
- **[결정]** 임무 경로 화면(`gui/widgets/mission_page.py`, 사이드바 결과 다음, Ctrl+4): 분석과 독립된 KMZ/KML 도구. 표 + QPainter 미리보기(위도 보정 등거리 근사, 축척 막대, 시작/끝 표시) + QGIS 내보내기(`geo.write_missions_gis`: 임무별 gpkg/geojson, 합본 missions.gpkg). 창 전체에 KMZ 만 놓으면 이 화면이 받는다. 면적은 폴리곤 중심의 UTM 존으로 투영해 계산.
- **[결정]** 앱 아이콘을 쿼드콥터 평면도(프로펠러 4 + 동체) 로 바꿨다 (`icons._PATHS["logo"]`, `packaging/make_icon.py` 로 ico/png 재생성). 사이드바 로고·창 아이콘·exe 아이콘이 같은 경로를 쓴다.
- **[결정]** 임무 경로 화면의 미리보기를 QPainter 에서 보고서와 같은 Plotly 지도(`report/mission_map.py`, `templates/mission_map.html.j2`, QWebEngineView) 로 바꿨다: UTM(m, 절대 E/N + EPSG)·위경도 전환(아핀 연동), 휠 확대, 위성 배경(위경도 보기에서만; 확대하면 타일을 다시 받음), hover 좌표. 타일 합치기 JS 는 `templates/_basemap.js.j2` 하나로 두고 보고서·임무 지도가 함께 쓴다. WebEngine 이 없거나 `FLIGHTQA_NO_WEBVIEW` 면 QPainter 미리보기로 대체(테스트). 여럿을 불러오면 기본은 전부 보이는 범위, 행 선택 시 그 임무로 확대.
- **[확정]** 페이지 아래가 잘려 보이던 것은 임무 화면이 스크롤 없이 고정 높이였기 때문. 결과 화면과 같은 전체 페이지 스크롤로 바꾸고 지도는 540 px 고정.
- **[결정]** 파일·폴더 대화상자의 기본 위치는 사용자 홈 폴더(`common.home_dir`). 프리셋 대화상자만 프리셋 폴더, PDF 저장만 결과 폴더를 유지. "비우기" 류 버튼은 모두 빨간 박스 + 휴지통(`variant="danger"`).
- **[확정]** MainWindow 에서 임무 화면이 설정 화면보다 먼저 만들어지면 배경지도 설정을 읽는 콜백이 `settings` 가 없어 AttributeError 를 낸다 (실측). 설정 화면을 먼저 만들고 스택에는 PAGES 순서로 넣는다.
- **[결정]** 위경도 지도(보고서·임무 화면 모두)는 EPSG:4326 을 그대로: 경도 1° 와 위도 1° 를 같은 화면 길이로 (`scaleratio = 1`). 이전의 cos(위도) 보정은 "UTM 처럼 보인다" 는 사용자 지적으로 뺐다. 위성 타일은 위경도에 선형으로 늘려 넣으므로 이 보기에서는 가로로 늘어난 형태가 정상이며, QGIS 의 위경도 보기와 같다.
- **[결정]** UTM(m) 지도에도 위성 배경: `FQBasemap.loadWarped` 가 현재 축 범위의 화소마다 아핀(UTM→위경도)으로 역변환해 웹 메르카토르 모자이크를 축 좌표계로 재투영한 이미지 한 장을 만든다 (최대 1400 px, 최근접 표본). 조사지 규모(수 km)에서 아핀 오차는 m 미만. 보고서의 UTM 지도(상대좌표) 와 임무 지도(절대 UTM) 둘 다 같은 함수를 쓰고, 보기 전환·확대 시 타일을 다시 받는다.
- **[확정]** 임무 지도의 축 제목 여백이 보고서와 달랐던 원인은 레이아웃을 따로 만들었기 때문(`ticklabelstandoff` 없음, 여백 값 다름). `charts._layout` 과 같은 여백·눈금 여백을 쓰도록 통일했다.
- **[결정]** 임무 표의 생성 시각은 한국표준시(UTC+9) 로 표시하고 열 이름을 "생성 (KST)" 로. 파일·GeoPackage 속성은 UTC ISO 그대로.
- **[결정]** `danger` 버튼(비우기·제거·캐시 비우기)의 아이콘 색은 글자와 같은 상태색(빨강).
## 2026-10-01 (보고서 지도 통합 · 용어 · 아이콘)

- **[확정]** 보고서 범례에는 "추정 조사 구역" 이 있는데 지도에 안 보이던 원인: 여유 폭 0 인 추정 구역은 바깥 조사 라인과 정확히 겹치는데, 맨 아래 트레이스로 그려 궤적 선과 캡처 점에 가렸다. 외곽선을 맨 위(채움 없이 2.5 px)로 옮기고 범례 순서는 `legendrank` 로 유지한다.
- **[결정]** 보고서의 궤적 지도 두 장(UTM·위경도)을 임무 경로 화면과 같은 한 장으로 합쳤다: [UTM (m) | 위경도] 전환, 위성 배경, 전체 보기, 범례 하나. 두 Plotly 지도는 그대로 두되 하나만 보이고(아핀 연동 유지), 범례 클릭은 이름으로 두 지도의 같은 트레이스를 모두 켜고 끈다(링이 여러 개인 커버리지 포함). 인쇄·PDF 이미지는 보이는 좌표계 것만 만든다. 지도 높이 480 px.
- **[결정]** 로고 SVG 는 `theme/tokens.LOGO_SVG` 한 곳에서 정의하고 GUI 아이콘 세트·앱 아이콘·보고서 상단·파비콘이 함께 쓴다 (보고서 상단에 옛 맥박선 로고가 하드코딩돼 남아 있었다).
- **[결정]** 용어: "대지속도" → "비행속도" (ground speed 는 현장에서 잘 쓰지 않음, 영어 표기도 Flight speed), "N 완전" → "밴드 누락 없음 N건". GUI 상세 표의 "[게이트]" 는 보고서와 같은 GATE 배지로.

## 2026-10-07 (후처리 품질 — Metashape 보고서)

- **[결정]** Metashape 처리 보고서(PDF) 판정을 분석과 독립된 화면(후처리 품질, Ctrl+5)·명령(`flightqa metashape`)으로 추가했다. HTML 보고서에 넣는 것은 2 단계로 미룸. DJI Terra 보고서는 구조가 달라 비범위.
- **[확정]** 보고서는 Qt 가 만든 텍스트 PDF 라 `pypdf`(BSD) 만으로 읽힌다 — Pillow·OCR 불필요. 추출 텍스트의 버릇(그림자 중복 줄, "T ie Points" 같은 글자 사이 공백, 라벨 다음 줄이 값, 보정 표의 이름·값 두 번 반복)은 [metashape_report.md](metashape_report.md) 에 기록. 그림은 DCTDecode(JPEG) XObject 라 그대로 꺼내 화면에 보인다(잔차 그림은 벡터라 제외).
- **[확정]** 다중분광 보고서는 K3 가 고정 계수라 오차·상관이 없고 머리글 맨 끝·행 맨 앞에 온다. 중복 줄 합치기로 머리글의 K3 와 행의 K3 가 한 줄이 되므로, 머리글 다음이 숫자면 그 줄을 행으로도 본다. 카메라 표의 긴 이름은 `"RedEdge-M, Red edge (5…"` 처럼 잘려 보정 페이지 이름과 괄호 앞까지만 비교한다 (정확 일치 우선 — "Red" 가 "Red edge" 를 삼키지 않게).
- **[확정]** 위치 오차 표의 단위는 괄호에 있다: RTK 기체(M3M) 보고서는 cm, RedEdge(카메라 GPS) 는 m. 파서가 m 로 환산하고 원 단위를 보존한다. 위치 오차 기준 프로필은 카메라 이름(MicaSense/RedEdge → gps, 그 외 → rtk)으로 자동.
- **[가정]** GCP 표는 샘플에 없어 Metashape 매뉴얼의 열 순서(Count, X, Y, Z, XY, Total)로만 읽는다. 실제 GCP 보고서가 오면 확인.
- **[가정]** 임계값(정렬 100/95 %, RMS 1.0/1.5 px, 최대 30 px, 다중도 3, 상관 0.95, RTK 10/30 cm, GPS 3/10 m)은 Agisoft 가이드의 통상 권고와 샘플 두 부로 정한 초기값. 샘플 판정: RGB → WARN(RMS 1.08, 최대 44.6 px, K2–K3 −0.97), 다중분광 → WARN(정렬 1,520/1,540).
- **[확정]** 비행 분석 대조를 실제 산림 조사지(RedEdge 308 캡처)로 돌려 보니 영상 수는 정확히 일치(1,540)했지만 고도 82 m vs AGL 118 m(31 %), GSD 5.67 vs 8.18 cm(31 %), 면적 13.0 ha vs 추정 조사 구역 4.8 ha(168 %)로 WARN 셋이 쌓여 전체 FAIL 이 됐다. 원인은 정의 차이: Metashape 고도는 재구성 표면(수관) 기준이고, Coverage area 는 영상 자취 합집합(패널 캡처 포함)이다. → 면적은 FlightQA 커버리지 합집합(9.9 ha, 32 %)과 비교하고 허용을 50 % 로, 고도 근거 문장에 수관 기준임을 덧붙이고, 대조 그룹은 전체 판정에 WARN 까지만 반영(WARN 누적 제외). 재실행: 전체 WARN(정렬 + 고도·GSD 대조 경고), 면적 PASS.

## 2026-10-08 (권고 조치 문구 · 용어 · 시작 카드)

- **[결정]** "다음 행동"·"권고 행동" → **"권고 조치"** 로 통일 (GUI 상세 표 머리글, 보고서 ko/en, CLI, 판정 카드 문구). 영어는 "Recommended action".
- **[결정]** 권고 조치 문구 약 60건을 개조식으로 다시 썼다: 첫 줄 조치(명사형, 가운뎃점으로 병렬), 둘째 줄 `영향:`/`원인:`. `split_sentences` 가 줄바꿈도 경계로 보도록 바꿨고 규칙은 DESIGN.md §7 에 적었다. 판정 로직·키는 그대로라 테스트는 문구 검사만 바뀌었다.
- **[결정]** 직역 용어 정리(DESIGN.md §7 표): 중첩→중복도, 직하→연직, 구멍→공백, 이벤트→구간, RTK 고정→고정해, 다중도→관측 영상 수 등. 설정 키·CSV 열·GeoJSON 레이어 이름은 바꾸지 않았다(호환).
- **[확정]** exe 시작이 느린 이유는 파이썬 모듈 불러오기였다: 메인 창 모듈 import 가 6.5 s(개발 환경, 캐시 있음)이고 그중 분석 화면 → 워커 → 파이프라인 → `pvlib`(scipy 포함 2 s)·보고서(plotly) 가 5.2 s. 워커가 파이프라인·보고서를 분석 시작 때 불러오고 `pvlib` 은 태양각 함수 안에서 불러오도록 바꿔 2.8 s 로 줄였다. 남은 1.5 s 는 임무 화면의 shapely·tifffile.
- **[결정]** 시작 카드(`gui/splash.py`): `QApplication` 직후 테두리 없는 420×180 카드(로고·이름·버전·단계 문구·진행 막대)를 띄우고 메인 창이 보이면 닫는다. 테마 토큰을 그대로 쓴다.
- **[확정]** exe 실측(창 가시화 시각, 2회 평균): Qt 시작 카드 1.2–1.4 s, 메인 창 3.4–3.6 s. 그 앞 1 초 남짓은 부트로더·Python·Qt DLL 로드라 Qt 로는 덮을 수 없어 PyInstaller 부트로더 스플래시(Tcl/Tk)를 추가했다: `packaging/make_splash.py` 가 Qt 카드를 그대로 PNG(`assets/splash.png`, 라이트 테마 고정)로 그리고, Qt 카드가 뜨면 `pyi_splash.close()` 로 넘긴다. tkinter 모듈은 계속 제외하며 부트로더가 tcl/tk DLL 만 동봉한다.
- **[확정]** 부트로더 스플래시 적용 후 실측(창 가시화): 첫 창(Tk 스플래시) 0.14 s(캐시 상태) / 1.8 s(첫 실행), 메인 창 3.6 s / 5.3 s. 실행 직후 같은 카드가 보이고 Qt 카드로 자연스럽게 이어진다. zip 은 343 → 345 MB.

## 2026-10-19 (목표고도 자동화 · 경사 촬영 제외 · 설치 폴더)

- **[확정]** 고정 목표고도 120 m 가 기본값이면 현장마다 다른 임무 고도(현장 C 50 m, 현장 D 300 m)가 전부 FAIL 로 나왔다(사용자 지적, 홈 화면의 최근 목록이 모두 실패). 목표의 출처를 설정 숫자 → 임무 KMZ 촬영 고도(`globalShootHeight`, 없으면 `height`) → 조사 라인 중앙값 순으로 바꾸고 기본값을 `"auto"` 로 두었다. 임무 매칭을 궤적 분석 앞으로 옮겨 목표의 출처로 쓴다. 속도도 같은 방식(`autoFlightSpeed`).
- **[확정]** DJI 고도 최적화(Elevation Optimization): 임무 마지막에 짐벌 -45° 로 중심을 향해 경사 촬영을 한다. 현장 D 136 캡처 중 끝 10장이 45.1° 로 기록돼 연직 이탈 7.4 % FAIL 이 났고 조사 라인 수(10)와 커버리지(62.75 ha)도 부풀었다. 30° 이상이 3장 이상 이어지면 경사 촬영 구간으로 보아 판정·라인·중복도에서 빼고 `flight.oblique` 정보 항목으로 보인다. 재실행: PASS, 라인 9, 조사 캡처 126.
- **[결정]** 빌드 산출물 폴더 이름을 `_out/dist` → `_out/release` 로 바꾸고, 빌드 끝에 설치 폴더(`D:\seongjun\06_software\FlightQA`, `FLIGHTQA_INSTALL_DIR` 로 변경)로 복사한 뒤 바탕화면·시작 메뉴 바로가기를 만든다. 저장소는 `D:\seongjun\04_code\flightqa` 로 옮겼다(00_test 에서 졸업).
- **[결정]** 2026-10-19 (같은 날 수정) 사용자 지적: 실행 파일이 숨김 폴더 `_out\release` 깊숙이 있고 저장소 루트가 지저분하다. 저장소를 `D:\seongjun\04_code\flightqa\dev\` 로 한 단계 내리고, 상위 폴더를 앱 폴더로 써서 `FlightQA.exe`·`시작하기.txt`·`docs`·`tools` 만 바로 보이게 했다(`_internal` 숨김). PyInstaller onedir 은 exe 옆에 `_internal` 이 있어야 하고 pyproject·uv.lock·CLAUDE.md 등은 도구가 저장소 루트에서 찾으므로, 두 세계를 한 폴더에 섞는 대신 폴더를 포개는 쪽을 택했다. 배포 단계는 `dev\` 를 지우지 않도록 폴더 전체 /MIR 대신 항목별 교체로 바꿨다. 별도 설치 폴더 `06_software\FlightQA` 는 더 쓰지 않는다.

