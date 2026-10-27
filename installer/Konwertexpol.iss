; Inno Setup 6 script - builds Konwertexpol_Setup.exe from installer\payload (see build_installer.bat)
#define AppName "Konwertexpol"
#define AppVersion "1.0"

[Setup]
AppId={{6F0D5B1E-2C4A-4E7B-9A31-5D2E8C7F1A90}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=IT Guy
AppPublisherURL=https://www.facebook.com/ajtiguy
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=output
OutputBaseFilename=Konwertexpol_Setup
SetupIconFile=payload\icons\konwertexpol.ico
UninstallDisplayIcon={app}\icons\konwertexpol.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
InfoBeforeFile=payload\INSTALLER_NOTICE.txt
LicenseFile=payload\LICENSE

[Files]
Source: "payload\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
; the tools folder must stay writable for normal users, otherwise "Update tools" and paths.txt would need administrator rights
Name: "{app}\tools"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\{#AppName} - update tools"; Filename: "{app}\update_tools.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName} - read me"; Filename: "{app}\README.txt"
Name: "{autoprograms}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
; an older per-user menu (from install.bat) would hide the machine-wide one - remove it first, as the normal user
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" uninstall-user"; Flags: runhidden runasoriginaluser waituntilterminated
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" install --machine"; Flags: runhidden waituntilterminated; StatusMsg: "Adding Konwertexpol to the right-click menu..."
; lets Explorer show the menu also when more than 15 files are selected (per user; needs Explorer restart / sign-in)
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" multi-on"; Flags: runhidden runasoriginaluser waituntilterminated

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" uninstall"; Flags: runhidden; RunOnceId: "RemoveKonwertexpolMenu"

[UninstallDelete]
; also what was added after the installation (updated tools, caches)
Type: filesandordirs; Name: "{app}"
