; Inno Setup Script for PitchGrid Mapper
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

#define ApplicationName "PitchGrid Mapper"
#define ApplicationExeName "PitchGrid Mapper"
#define CompanyName "PitchGrid"
#define PublisherURL "https://github.com/peterjungx/pitchgrid-mapper"

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
OutputBaseFilename=PitchGrid-Mapper-{#VERSION_STRING}-Setup
SetupIconFile={#BUILDS_PATH}\PitchGrid Mapper.ico
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
  RegKey32: String;
  Version: String;
begin
  Result := False;
  RegKey := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  RegKey32 := 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

  // Check user install
  if RegQueryStringValue(HKEY_CURRENT_USER, RegKey, 'pv', Version) then
  begin
    if Version <> '' then
      Result := True;
  end;

  // Check machine install (32-bit registry view via WOW6432Node - most common location)
  if not Result then
  begin
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey32, 'pv', Version) then
    begin
      if Version <> '' then
        Result := True;
    end;
  end;

  // Check machine install (native path)
  if not Result then
  begin
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey, 'pv', Version) then
    begin
      if Version <> '' then
        Result := True;
    end;
  end;

  // Check 64-bit machine install
  if not Result and IsWin64 then
  begin
    if RegQueryStringValue(HKEY_LOCAL_MACHINE_64, RegKey, 'pv', Version) then
    begin
      if Version <> '' then
        Result := True;
    end;
  end;

  // Also check for Edge browser (includes WebView2)
  if not Result then
  begin
    RegKey := 'Software\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}';
    RegKey32 := 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}';
    if RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey32, 'pv', Version) or
       RegQueryStringValue(HKEY_LOCAL_MACHINE, RegKey, 'pv', Version) or
       RegQueryStringValue(HKEY_LOCAL_MACHINE_64, RegKey, 'pv', Version) then
    begin
      if Version <> '' then
        Result := True;
    end;
  end;
end;

function InitializeSetup: Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if not IsWebView2Installed then
  begin
    if MsgBox('PitchGrid Mapper requires Microsoft Edge WebView2 Runtime.'#13#10#13#10 +
              'Would you like to download it now?', mbConfirmation, MB_YESNO) = IDYES then
    begin
      ShellExec('open', 'https://go.microsoft.com/fwlink/p/?LinkId=2124703', '', '', SW_SHOW, ewNoWait, ErrorCode);
    end;
    // Allow installation to continue - user may install WebView2 later
  end;
end;
