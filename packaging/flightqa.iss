; Inno Setup 6 스크립트 (선택). build_windows.ps1 이 ISCC.exe 를 찾으면 호출한다.
;   ISCC.exe /DAppVersion=0.1.0 packaging\flightqa.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7C4B4E6A-2A6D-4F0E-9E3B-FLIGHTQA0001}
AppName=FlightQA
AppVersion={#AppVersion}
AppPublisher=NIFoS National Forest Satellite Information & Research Center
DefaultDirName={autopf}\FlightQA
DefaultGroupName=FlightQA
OutputDir=..\_out\dist
OutputBaseFilename=FlightQA-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
WizardStyle=modern

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\_out\dist\FlightQA\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FlightQA"; Filename: "{app}\FlightQA.exe"
Name: "{autodesktop}\FlightQA"; Filename: "{app}\FlightQA.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "바탕화면 아이콘 만들기"; GroupDescription: "추가 작업:"

[Run]
Filename: "{app}\FlightQA.exe"; Description: "FlightQA 실행"; Flags: nowait postinstall skipifsilent
