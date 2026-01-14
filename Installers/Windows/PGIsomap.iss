; Inno Setup Script for PG Isomap
; Requires Inno Setup 6.x

#ifndef VERSION_STRING
  #define VERSION_STRING "0.1.0"
#endif
#ifndef VERSION_NUMBERS
  #define VERSION_NUMBERS "0.1.0"
#endif
#ifndef BUILDS_PATH
  #define BUILDS_PATH "..\.."
#endif

#define ApplicationName "PG Isomap"
#define ApplicationExeName "PGIsomap"
#define CompanyName "PitchGrid"
#define PublisherURL "https://github.com/nodeaudio/pg_isomap"

[Setup]
; Application metadata
AppId={{A7E8F3D2-1234-5678-9ABC-DEF012345678}
AppName={#ApplicationName}
AppVersion={#VERSION_NUMBERS}
AppVerName={#ApplicationName} {#VERSION_STRING}
AppPublisher={#CompanyName}
AppPublisherURL={#PublisherURL}
AppSupportURL={#PublisherURL}
AppUpdatesURL={#PublisherURL}

; Installation settings
DefaultDirName={autopf}\{#CompanyName}\{#ApplicationName}
DefaultGroupName={#ApplicationName}
OutputBaseFilename=PGIsomap-{#VERSION_STRING}-Setup
SetupIconFile={#BUILDS_PATH}\PGIsomap.ico
Compression=lzma2/ultra64
SolidCompression=yes

; Windows requirements
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

; UI settings
WizardStyle=modern
DisableProgramGroupPage=yes

; Privileges (per-user install by default)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Main application directory (PyInstaller onedir output)
Source: "{#BUILDS_PATH}\dist\{#ApplicationExeName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#ApplicationName}"; Filename: "{app}\{#ApplicationExeName}.exe"
Name: "{group}\{cm:UninstallProgram,{#ApplicationName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#ApplicationName}"; Filename: "{app}\{#ApplicationExeName}.exe"; Tasks: desktopicon

[Run]
; Optionally run after install
Filename: "{app}\{#ApplicationExeName}.exe"; Description: "{cm:LaunchProgram,{#StringChange(ApplicationName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Check for WebView2 Runtime
function IsWebView2Installed: Boolean;
var
  RegKey: String;
begin
  Result := False;
  // Check user install
  RegKey := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  if RegKeyExists(HKEY_CURRENT_USER, RegKey) then
    Result := True
  // Check machine install
  else if RegKeyExists(HKEY_LOCAL_MACHINE, RegKey) then
    Result := True
  // Check 64-bit machine install
  else if IsWin64 and RegKeyExists(HKEY_LOCAL_MACHINE_64, RegKey) then
    Result := True;
end;

function InitializeSetup: Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if not IsWebView2Installed then
  begin
    if MsgBox('PG Isomap requires Microsoft Edge WebView2 Runtime.'#13#10#13#10 +
              'Would you like to download it now?', mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://go.microsoft.com/fwlink/p/?LinkId=2124703', '', '', SW_SHOW, ewNoWait, ErrorCode);
    end;
    // Allow installation to continue - user may install WebView2 later
  end;
end;
