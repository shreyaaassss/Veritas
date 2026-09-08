; Veritas DPDPA Compliance Platform — Windows Installer
; Built with Inno Setup 6.7+
; Produces: VeritasSetup-1.0.0.exe
;
; Build:
;   "C:\Users\DELL\AppData\Local\Programs\Inno Setup 6\ISCC.exe" veritas-installer.iss

#define AppName      "Veritas"
#define AppFullName  "Veritas DPDPA Compliance Platform"
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppPublisher "Veritas Technologies"
#define AppURL       "https://veritas.io"

[Setup]
AppId={{B7F3A2C1-4D5E-4F6A-8B9C-0D1E2F3A4B5C}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppVerName={#AppFullName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}

DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=no

OutputDir=..\installer\output
OutputBaseFilename=veritas_{#AppVersion}_windows_amd64

; Administrator required for Program Files + Windows Service
PrivilegesRequired=admin

; Compression (LZMA2 — best ratio for the large runtime exe)
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes

WizardStyle=modern
WizardSizePercent=120
DisableWelcomePage=no
ShowLanguageDialog=no

UninstallDisplayName={#AppFullName}
UninstallDisplayIcon={app}\veritas-launcher.exe
CreateUninstallRegKey=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut to the Veritas Dashboard"; GroupDescription: "Additional shortcuts:"

[Files]
; Go launcher — small, validates license + manages Windows Service
Source: "..\dpdpa-agent\dist\veritas-launcher.exe"; DestDir: "{app}"; Flags: ignoreversion

; Python runtime bundle — large (~671 MB), contains full compliance engine
Source: "..\dpdpa-agent\dist\veritas-runtime.exe"; DestDir: "{app}"; Flags: ignoreversion

; NOTE: veritas.vlic is copied via [Code] CurStepChanged after user selects it

[Icons]
; Start Menu
Name: "{group}\Open Veritas Dashboard";   Filename: "http://localhost:8000";   IconFilename: "{app}\veritas-launcher.exe"; Comment: "Open the Veritas compliance dashboard"
Name: "{group}\Veritas Service Status";   Filename: "{app}\veritas-launcher.exe"; Parameters: "status"; Comment: "Check Veritas service status"
Name: "{group}\Uninstall Veritas";        Filename: "{uninstallexe}"

; Desktop shortcut (optional)
Name: "{autodesktop}\Veritas Dashboard";  Filename: "http://localhost:8000"; IconFilename: "{app}\veritas-launcher.exe"; Tasks: desktopicon

[Run]
; Register and start the Windows Service
Filename: "{app}\veritas-launcher.exe"; Parameters: "install"; StatusMsg: "Registering Veritas Windows Service..."; Flags: runhidden waituntilterminated
Filename: "{app}\veritas-launcher.exe"; Parameters: "start";   StatusMsg: "Starting Veritas...";                    Flags: runhidden waituntilterminated

; Offer to open dashboard when installer finishes
Filename: "http://localhost:8000"; Description: "Open Veritas Dashboard in browser (wait ~60s for first startup)"; Flags: postinstall shellexec skipifsilent unchecked

[UninstallRun]
Filename: "{app}\veritas-launcher.exe"; Parameters: "stop";      Flags: runhidden waituntilterminated; RunOnceId: "StopSvc"
Filename: "{app}\veritas-launcher.exe"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveSvc"

[Code]
var
  LicFilePage: TInputFileWizardPage;

{ ---- Wizard setup ---- }

procedure InitializeWizard;
begin
  LicFilePage := CreateInputFilePage(
    wpLicense,
    'Veritas License File',
    'Provide your license file to activate Veritas.',
    'Your license file (.vlic) was provided with your Veritas purchase.'
  );
  LicFilePage.Add(
    'License File (.vlic):',
    'Veritas License Files|*.vlic|All Files|*.*',
    '.vlic'
  );
end;

{ ---- Validate license file selection before proceeding ---- }

function NextButtonClick(CurPageID: Integer): Boolean;
var
  LicPath: String;
begin
  Result := True;

  if CurPageID = LicFilePage.ID then
  begin
    LicPath := LicFilePage.Values[0];

    if LicPath = '' then
    begin
      MsgBox('Please select your Veritas license file (.vlic) to continue.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if LowerCase(ExtractFileExt(LicPath)) <> '.vlic' then
    begin
      MsgBox('The selected file does not appear to be a valid Veritas license file. Please select a .vlic file.', mbError, MB_OK);
      Result := False;
      Exit;
    end;

    if not FileExists(LicPath) then
    begin
      MsgBox('File not found: ' + LicPath, mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

{ ---- Copy .vlic to install directory after files are laid down ---- }

procedure CurStepChanged(CurStep: TSetupStep);
var
  LicSrc, LicDest: String;
begin
  if CurStep = ssPostInstall then
  begin
    LicSrc  := LicFilePage.Values[0];
    LicDest := ExpandConstant('{app}\veritas.vlic');

    if LicSrc <> '' then
    begin
      if not CopyFile(LicSrc, LicDest, False) then
        MsgBox('Warning: Could not copy license file to installation directory. ' +
               'Please manually copy your .vlic file to: ' + ExpandConstant('{app}'),
               mbInformation, MB_OK);
    end;
  end;
end;
