; TallyFlow Connector -- Windows installer (Inno Setup 6).
;
; Build:  python run.py connector
; Output: apps/connector/dist/installer/TallyFlowConnector-Setup-<version>.exe
;
; What this installer is responsible for:
;   1. copying two executables,
;   2. collecting the connector id + secret from the TallyFlow app,
;   3. handing them to `tally-connector.exe install`, which writes the config
;      and registers the logon task.
;
; Deliberately *not* responsible for: writing connector.json, or talking to
; Task Scheduler. That logic lives in Python where it is unit tested; Pascal
; Script here is only a form.
;
; Per-user install (PrivilegesRequired=lowest), so:
;   * no UAC prompt -- a shop owner can install this without calling anyone,
;   * {app} lands under %LOCALAPPDATA%\Programs and is writable, which matters
;     because connector.json is written next to the executable,
;   * the logon task runs as the person who installed it, which is the account
;     TallyPrime is running under.

#define AppName          "TallyFlow Connector"
#define AppPublisher     "TallyFlow"
#define ExeName          "tally-connector.exe"
#define ServiceExeName   "tally-connector-service.exe"
#define DefaultBackend   "wss://api-tallyflow.jsrprimesolution.com/v1/connector"

; Overridden by the build script (ISCC /DAppVersion=...) so the installer name
; cannot drift from tally_connector.__version__.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Never change this: it is how Windows recognises an upgrade rather than a
; second, parallel installation. The doubled brace is Inno's escape.
AppId={{C0466045-89F3-4616-A864-20597F034E26}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; Points at the site's setup guide, which is a page that exists. A dead
; support link in Add/Remove Programs is found by the one person already stuck.
AppSupportURL=https://tallyflow.jsrprimesolution.com/docs
DefaultDirName={autopf}\TallyFlow Connector
DefaultGroupName=TallyFlow
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=TallyFlowConnector-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ExeName}
; Nothing here needs a reboot; saying so up front avoids a scary prompt.
AlwaysRestart=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\{#ExeName}";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#ServiceExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "check-tally.cmd";           DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Check Tally Connection"; Filename: "{app}\check-tally.cmd"; \
      Comment: "Test whether the connector can read TallyPrime on this computer"
Name: "{group}\Connector Status";       Filename: "{app}\check-tally.cmd"; Parameters: "status"; \
      Comment: "Is the background connector paired and running?"
Name: "{group}\Connector Logs";         Filename: "{localappdata}\TallyFlow Connector\logs"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"

[Run]
; The pairing details go through a file in {tmp}, never on the command line:
; any process on the machine can read another process's arguments.
Filename: "{app}\{#ExeName}"; Parameters: "install --from-file ""{tmp}\pairing.txt"""; \
      StatusMsg: "Pairing this computer and starting the connector..."; \
      Flags: runhidden waituntilterminated; Check: not IsUpgrade
; An upgrade has no credentials to pass -- the secret is shown once, at pairing
; time, and is not recoverable here. `install` with no id/secret re-registers
; the startup task against the pairing already on disk. Without this branch a
; silent self-update would replace the executables and never start them again.
Filename: "{app}\{#ExeName}"; Parameters: "install"; \
      StatusMsg: "Updating the connector..."; \
      Flags: runhidden waituntilterminated; Check: IsUpgrade
Filename: "{app}\check-tally.cmd"; Description: "Check the connection to TallyPrime now"; \
      Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
; --purge, unlike the bare `uninstall` run during an upgrade: removing the
; product removes the credential with it. The deletion lives in Python because
; that is where it is unit tested, and because a manual install (no Inno) has
; to be removable the same way.
Filename: "{app}\{#ExeName}"; Parameters: "uninstall --purge"; Flags: runhidden; \
      RunOnceId: "RemoveStartupTask"

[UninstallDelete]
; Belt and braces for the case the executable is already gone or failed to run
; -- the uninstaller must not leave a shop's secret on disk either way. Both
; entries are no-ops when --purge above succeeded.
Type: files;           Name: "{app}\connector.json"
Type: filesandordirs;  Name: "{localappdata}\TallyFlow Connector"

[Code]
var
  PairPage: TInputQueryWizardPage;
  UpgradeChecked: Boolean;
  UpgradeCached: Boolean;

function IsUpgrade: Boolean;
var
  Dir: String;
begin
  // An existing connector.json means this machine is already paired, so there
  // is nothing to ask for and nothing to overwrite. Decided from the file
  // rather than from Inno's own upgrade detection because a self-update reaches
  // this code exactly the way a hand-run installer does.
  //
  // WizardDirValue, NOT ExpandConstant of the app constant. This is the whole
  // reason the function looks like this:
  //
  //   The pairing page is created at wpWelcome, so ShouldSkipPage asks this
  //   question before Setup has initialised its install directory. Expanding
  //   the app constant there aborts the installer outright --
  //
  //     Runtime error (at 1:111): Internal error: An attempt was made to
  //     expand the "app" constant before it was initialized.
  //
  //   -- on the FIRST page a customer sees, with no way past it. WizardDirValue
  //   is filled in when the wizard is built, from DefaultDirName or, on an
  //   upgrade, from the previous install (UsePreviousAppDir). It is therefore
  //   valid everywhere this is called, which the app constant is not.
  //
  // Not cached until the value is actually available, so an early call cannot
  // freeze a wrong answer in. Cached afterwards so every later caller -- the
  // two [Run] entries, ShouldSkipPage, NextButtonClick, CurStepChanged -- gets
  // one consistent answer rather than re-reading a directory that setup is
  // midway through writing.
  //
  // This comment uses // rather than braces because a { } comment ends at the
  // FIRST closing brace and Pascal comments do not nest, so naming an Inno
  // constant inside one terminates it early. The bracketed section names above
  // are kept off the start of a line for the same class of reason: the section
  // scanner strips leading whitespace, and reads such a line as a section tag.
  if not UpgradeChecked then begin
    Dir := WizardDirValue;
    if Dir <> '' then begin
      UpgradeCached := FileExists(AddBackslash(Dir) + 'connector.json');
      UpgradeChecked := True;
    end;
  end;
  Result := UpgradeCached;
end;

procedure InitializeWizard;
begin

  PairPage := CreateInputQueryPage(wpWelcome,
    'Connect to your TallyFlow account',
    'Enter the pairing details shown in the TallyFlow app.',
    'On your phone, open TallyFlow, go to Settings > Connectors, and tap' + #13#10 +
    '"Add this computer". Type the Connector ID and Secret it shows below.' + #13#10 + #13#10 +
    'The secret is displayed only once, so keep the app open until this' + #13#10 +
    'installer finishes.');

  PairPage.Add('Connector ID:', False);
  PairPage.Add('Connector secret:', False);
  PairPage.Add('Server address:', False);

  { Pre-seeded from the command line so a business with twenty shop PCs can
    push this out per machine instead of typing a secret twenty times:

      TallyFlowConnector-Setup.exe /VERYSILENT /ID=<id> /SECRET=<secret>

    Each machine still needs its own pair -- one connector row per PC is what
    lets the app say which shop went offline. }
  PairPage.Values[0] := ExpandConstant('{param:ID|}');
  PairPage.Values[1] := ExpandConstant('{param:SECRET|}');
  PairPage.Values[2] := ExpandConstant('{param:SERVER|{#DefaultBackend}}');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  { Nothing to ask when both were supplied on the command line, and nothing to
    ask on an upgrade -- the pairing being preserved is already on disk. }
  Result := (PageID = PairPage.ID) and
            (IsUpgrade or
             ((ExpandConstant('{param:ID|}') <> '') and
              (ExpandConstant('{param:SECRET|}') <> '')));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Server: String;
begin
  Result := True;
  if (CurPageID <> PairPage.ID) or IsUpgrade then
    exit;

  if Trim(PairPage.Values[0]) = '' then begin
    MsgBox('Enter the Connector ID shown in the TallyFlow app.', mbError, MB_OK);
    Result := False;
    exit;
  end;

  if Trim(PairPage.Values[1]) = '' then begin
    MsgBox('Enter the Connector Secret shown in the TallyFlow app.' + #13#10 +
           'It is the long line of letters and numbers below the ID.',
           mbError, MB_OK);
    Result := False;
    exit;
  end;

  Server := Trim(PairPage.Values[2]);
  { The connector refuses any other scheme at startup. Catching it here means a
    typo is a message on this page rather than a connector that installs
    cleanly and then never comes online. }
  if (Pos('wss://', Server) <> 1) and (Pos('ws://', Server) <> 1) then begin
    MsgBox('The server address must start with wss:// (or ws:// for a local ' +
           'test server).', mbError, MB_OK);
    Result := False;
    exit;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Existing: String;
  ResultCode: Integer;
begin
  Result := '';
  { An upgrade cannot overwrite a running tally-connector-service.exe, so stop
    it and remove its task first. Pairing survives -- `uninstall` only touches
    Task Scheduler. }
  Existing := ExpandConstant('{app}\{#ExeName}');
  if FileExists(Existing) then
    Exec(Existing, 'uninstall', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  PairingFile: String;
begin
  PairingFile := ExpandConstant('{tmp}\pairing.txt');

  { Never written on an upgrade: the values would be blank, and `install`
    treats a blank id as a fatal error rather than as "keep what you have". }
  if (CurStep = ssInstall) and not IsUpgrade then begin
    SetArrayLength(Lines, 3);
    Lines[0] := 'id=' + Trim(PairPage.Values[0]);
    Lines[1] := 'secret=' + Trim(PairPage.Values[1]);
    Lines[2] := 'backend_url=' + Trim(PairPage.Values[2]);
    SaveStringsToFile(PairingFile, Lines, False);
  end;

  // The temp directory is removed at the end of setup anyway, but the secret
  // should not outlive the step that needed it -- setup can sit on its final
  // page for an hour. (Note: // comments here, because a Pascal brace comment
  // would be terminated by the first closing brace of an Inno constant.)
  if CurStep = ssPostInstall then
    DeleteFile(PairingFile);
end;
