; Windows installer for Chem SI Assistant (Inno Setup 6; SPEC section 13m.2, Phase 8).
;
; Made by the release workflow from the folder PyInstaller built:
;   iscc /DAppVersion=0.1.0 /DAppNumericVersion=0.1.0.0 /DSourceDir=<dist\Chem SI Assistant>
;        /DOutputDir=<folder> packaging\windows\installer.iss
; -> Chem-SI-Assistant-<version>-Windows-Setup.exe (the name the update check looks for).
;
; - Installs for the current user by default (in %LOCALAPPDATA%\Programs; no
;   administrator rights needed); "Install for all users" is offered as an option.
; - Start-menu shortcut, optional desktop shortcut, an uninstaller (Settings > Apps).
; - The AppId below never changes, so a newer version replaces the old one in place.
; - The update check (Help > Check for updates) starts this installer and then quits the
;   program; the installer waits up to 15 s for it to finish closing (it looks for the
;   mutex the running program holds, see si_assistant/resources.py: RUNNING_MUTEX).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppNumericVersion
  #define AppNumericVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\Chem SI Assistant"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define AppName "Chem SI Assistant"
#define AppExe "Chem SI Assistant.exe"
#define AppUrl "https://github.com/nathanohare/chem-si-assistant"
#define AppMutexName "ChemSIAssistant.Running"
#define AppUserModelID "ChemSIAssistant.App"

[Setup]
AppId={{DF97C8E6-8D0B-435D-8BDE-43AD337399D5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Chem SI Assistant contributors
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
VersionInfoVersion={#AppNumericVersion}
VersionInfoProductName={#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
UsePreviousAppDir=yes
; Per-user by default; the user may choose "all users" (that one asks for administrator rights).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\si_assistant\icons\app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=Chem-SI-Assistant-{#AppVersion}-Windows-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; (Files still in use are found by Windows' Restart Manager as a further safeguard.)
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; A new version's files replace the old ones completely (no left-over libraries).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "{#AppUserModelID}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// The running program holds a mutex named {#AppMutexName} (si_assistant/resources.py).
// The update check starts this installer and then closes the program: give it up to
// 15 seconds to finish (saving and closing the project); if it is still open after that,
// ask the user to close it (OK = try again, Cancel = stop).
function WaitForProgramToClose(): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  while CheckForMutexes('{#AppMutexName}') and (Waited < 15000) do
  begin
    Sleep(500);
    Waited := Waited + 500;
  end;
  Result := True;
  while CheckForMutexes('{#AppMutexName}') do
  begin
    if MsgBox('{#AppName} is still open. Please close it (File > Quit), then click OK.' + #13#10#13#10 +
              'Click Cancel to stop.', mbInformation, MB_OKCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := WaitForProgramToClose();
end;

function InitializeUninstall(): Boolean;
begin
  Result := WaitForProgramToClose();
end;
