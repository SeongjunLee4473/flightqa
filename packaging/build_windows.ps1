# FlightQA Windows 빌드 (CLAUDE.md §9): PyInstaller onedir → _out/release/FlightQA/ → zip → 앱 폴더 배포 + 바로가기.
# 사용:  powershell -ExecutionPolicy Bypass -File packaging/build_windows.ps1
# 주의:  이 파일은 UTF-8 BOM 으로 저장한다. Windows PowerShell 5.1 은 BOM 이 없으면 한글 경로(시작하기.txt)를 깨뜨린다.
# 요구:  uv, (선택) Inno Setup 6 (ISCC.exe 가 PATH 에 있으면 설치 프로그램도 만든다)
#
# 배포 폴더 구성 (사용자 눈에는 FlightQA.exe · 시작하기.txt · docs · tools 만 보인다):
#   FlightQA.exe          GUI (더블클릭)
#   시작하기.txt          한 쪽짜리 안내
#   docs\                 QA_RULES.md, ASSUMPTIONS.md, README.md
#   tools\flightqa-cli.cmd 명령줄 실행 래퍼
#   [숨김] _internal\ flightqa-cli.exe   — PyInstaller 구조상 exe 옆에 있어야 하므로 숨김 속성만
#   (zip 을 풀면 숨김 속성은 사라진다. 실행에는 영향 없음)
#
# 폴더 구성: 앱 폴더(기본값은 이 저장소의 상위 폴더, 예: D:\seongjun\04_code\flightqa) 에 FlightQA.exe 가 바로 보이고
# 개발 저장소는 그 안의 dev\ 하나로 모인다. 저장소 쪽 생성물은 전부 _out/ (release, build, cache) 아래로 모으고
# _out 과 .venv 에 숨김 속성을 준다. 빌드가 끝나면 앱 폴더(환경변수 FLIGHTQA_INSTALL_DIR 로 변경 가능)에 exe·_internal·
# docs·tools·시작하기.txt 만 교체해 넣고(dev\ 는 건드리지 않는다) 바탕화면·시작 메뉴에 FlightQA 바로가기를 만든다.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$out = "_out"
$dist = "$out/release"
$app = "$dist/FlightQA"

Write-Host "== 1. 의존성 동기화"
uv sync --all-groups
if ($LASTEXITCODE -ne 0) { throw "uv sync 실패" }

Write-Host "== 2. exiftool 번들 확인"
if (-not (Test-Path "tools/exiftool/exiftool.exe")) {
  uv run python scripts/fetch_exiftool.py
  if ($LASTEXITCODE -ne 0) { throw "exiftool 다운로드 실패" }
}

Write-Host "== 3. 빌드 정보·아이콘"
uv run python packaging/write_build_info.py
uv run python packaging/make_icon.py

Write-Host "== 4. PyInstaller → $app"
New-Item -ItemType Directory -Force $dist | Out-Null
if (Test-Path $app) {
  # 실행 중인 FlightQA.exe 가 폴더를 잠그면 여기서 멈춘다 — 앱을 닫고 다시 실행
  $running = Get-Process -Name FlightQA, flightqa-cli -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith((Resolve-Path $app).Path) }
  if ($running) { throw "FlightQA 가 실행 중입니다 (PID $($running.Id -join ', ')). 닫고 다시 빌드하세요." }
  Remove-Item -Recurse -Force $app
}
uv run pyinstaller packaging/flightqa.spec --noconfirm --clean --distpath $dist --workpath "$out/build"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 실패" }

Write-Host "== 5. 동봉 파일·폴더 정리"
New-Item -ItemType Directory -Force "$app/docs", "$app/tools" | Out-Null
Copy-Item README.md "$app/docs/README.md" -Force
Copy-Item docs/QA_RULES.md, docs/ASSUMPTIONS.md "$app/docs/" -Force
Copy-Item packaging/flightqa-cli.cmd "$app/tools/flightqa-cli.cmd" -Force
$ver = (uv run python -c "import flightqa; print(flightqa.__version__)").Trim()
$hash = (uv run python -c "import flightqa._build as b; print(b.GIT_HASH)").Trim()
$guide = Get-Content packaging/시작하기.txt -Raw -Encoding UTF8
$guide = $guide.Replace("{version}", $ver).Replace("{git_hash}", $hash).Replace("{build_date}", (Get-Date -Format "yyyy-MM-dd"))
[System.IO.File]::WriteAllText((Join-Path (Resolve-Path $app).Path "시작하기.txt"), $guide, (New-Object System.Text.UTF8Encoding $true))

Write-Host "== 6. 스모크 (exe 로 버전)"
& "$app/flightqa-cli.exe" version
if ($LASTEXITCODE -ne 0) { throw "flightqa-cli.exe version 실패" }
$viaCmd = (& cmd /c "`"$((Resolve-Path "$app/tools/flightqa-cli.cmd").Path)`" version" 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0 -or $viaCmd -notmatch "flightqa $([regex]::Escape($ver))") { throw "tools/flightqa-cli.cmd 실패: $viaCmd" }

$zip = "$dist/FlightQA-$ver-win64.zip"
Write-Host "== 7. zip → $zip"
if (Test-Path $zip) { Remove-Item $zip }
# 숨김 속성은 zip 다음에 준다: Compress-Archive 는 숨김 항목을 건너뛴다 (실측: _internal 이 빠져 25 MB).
Compress-Archive -Path $app -DestinationPath $zip
$zipInfo = Get-Item $zip
"{0}  {1} MB" -f $zipInfo.Name, [math]::Round($zipInfo.Length / 1MB, 1)
if ($zipInfo.Length -lt 200MB) { throw "zip 이 너무 작음 ($([math]::Round($zipInfo.Length/1MB,1)) MB) — _internal 누락 의심" }

$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($iscc) {
  Write-Host "== 8. Inno Setup"
  & $iscc.Source /DAppVersion=$ver packaging/flightqa.iss
} else {
  Write-Host "== 8. Inno Setup(ISCC.exe) 없음 — zip 배포만 생성"
}

Write-Host "== 9. 숨김 속성 (배포 폴더의 구성 요소, 저장소의 생성물 폴더)"
# 사용자가 볼 필요 없는 항목은 숨김 (실행에는 영향 없음). zip 과 설치 파일을 만든 뒤에 준다.
foreach ($h in @("_internal", "flightqa-cli.exe")) { attrib +h (Join-Path $app $h) }
foreach ($h in @($out, ".venv")) { if (Test-Path $h) { attrib +h $h } }

$install = if ($env:FLIGHTQA_INSTALL_DIR) { $env:FLIGHTQA_INSTALL_DIR } else { Split-Path -Parent $root }
Write-Host "== 10. 앱 폴더로 배포 → $install"
$busy = Get-Process -Name FlightQA, flightqa-cli -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -and (Test-Path $install) -and $_.Path.StartsWith((Resolve-Path $install).Path) }
if ($busy) {
  Write-Warning "앱 폴더의 FlightQA 가 실행 중(PID $($busy.Id -join ', ')) — 배포를 건너뜁니다. 앱을 닫고 다시 빌드하세요."
} else {
  New-Item -ItemType Directory -Force $install | Out-Null
  # 앱 폴더에는 저장소(dev\)가 함께 들어 있으므로 폴더 전체를 미러링하지 않고, 배포 항목만 하나씩 교체한다.
  # 하위 폴더는 /MIR 로 이전 버전 잔재까지 맞추고, 파일은 덮어쓴다. 숨김 속성은 아래에서 다시 준다.
  foreach ($d in @("_internal", "docs", "tools")) {
    robocopy (Join-Path $app $d) (Join-Path $install $d) /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 실패 ($d, $LASTEXITCODE)" }
  }
  foreach ($f in @("FlightQA.exe", "flightqa-cli.exe", "시작하기.txt")) {
    Copy-Item -LiteralPath (Join-Path $app $f) -Destination (Join-Path $install $f) -Force
  }
  foreach ($h in @("_internal", "flightqa-cli.exe")) { attrib +h (Join-Path $install $h) }
  $ws = New-Object -ComObject WScript.Shell
  $targets = @([Environment]::GetFolderPath("Desktop"), [Environment]::GetFolderPath("Programs"))
  foreach ($dir in $targets) {
    $lnk = $ws.CreateShortcut((Join-Path $dir "FlightQA.lnk"))
    $lnk.TargetPath = (Join-Path $install "FlightQA.exe")
    $lnk.WorkingDirectory = $install
    $lnk.IconLocation = (Join-Path $install "FlightQA.exe") + ",0"
    $lnk.Description = "FlightQA — UAV 다중분광 비행 데이터 품질 진단"
    $lnk.Save()
  }
  Write-Host "바로가기: 바탕화면, 시작 메뉴 (FlightQA.lnk)"
}
Write-Host "완료: $app → $install"
