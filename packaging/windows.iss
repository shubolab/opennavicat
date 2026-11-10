; Inno Setup 6+ template. The application payload architecture is verified before
; compilation. The default x86 Setup wrapper is intentional, including on ARM64.
#define AppVersion "@VERSION@"
#define SourceDir "@SOURCE_DIR@"
#define OutputDir "@OUTPUT_DIR@"
#define OutputName "@OUTPUT_NAME@"
#define PayloadArch "@ARCH@"

[Setup]
AppId={code:ApplicationId}
AppName=OpenNavicat
AppVersion={#AppVersion}
AppPublisher=OpenNavicat contributors
AppPublisherURL=https://github.com/shubolab/opennavicat
DefaultDirName={localappdata}\Programs\OpenNavicat
DefaultGroupName=OpenNavicat
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=@ALLOWED_ARCH@
ArchitecturesInstallIn64BitMode=@ALLOWED_ARCH@
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\OpenNavicat.exe
UninstallDisplayName=OpenNavicat {#AppVersion}
CloseApplications=no
RestartApplications=no
LicenseFile=@LICENSE@

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\OpenNavicat"; Filename: "{app}\OpenNavicat.exe"; WorkingDir: "{app}"; Check: not VerificationInstall

[Code]
function VerificationInstall: Boolean;
begin
  Result := ExpandConstant('{param:VERIFY|0}') = '1';
end;

function ApplicationId(Param: String): String;
begin
  if VerificationInstall then
    Result := 'org.opennavicat.verification.{#PayloadArch}'
  else
    Result := 'org.opennavicat.desktop';
end;
