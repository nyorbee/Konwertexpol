; Inno Setup 6 script - builds Konwertexpol_Setup.exe from installer\payload (see build_installer.bat)
; The setup asks for the language (English / Polski) first; the choice is also written to {app}\language.txt,
; so the installed program, its right-click menu and its tools installer use the same language.
; (This file is UTF-8 with BOM - keep it that way, it contains Polish letters.)
#define AppName "Konwertexpol"
#define AppVersion "1.3.0"

; the Polish wizard texts come with Inno Setup 6; if your copy lacks them, put Polish.isl next to this script
; (download: https://github.com/jrsoftware/issrc/tree/main/Files/Languages)
#define PlIsl AddBackslash(CompilerPath) + "Languages\Polish.isl"
#if !FileExists(PlIsl)
  #define PlIsl AddBackslash(SourcePath) + "Polish.isl"
#endif
#if !FileExists(PlIsl)
  #error Polish.isl not found - copy it next to Konwertexpol.iss (see the comment at the top of this file).
#endif

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
LicenseFile=payload\LICENSE
; always ask for the language first (do not guess from Windows, do not reuse the previous answer)
ShowLanguageDialog=yes
LanguageDetectionMethod=none
UsePreviousLanguage=no

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "payload\INSTALLER_NOTICE.txt"
Name: "pl"; MessagesFile: "{#PlIsl}"; InfoBeforeFile: "payload\INSTALLER_NOTICE_PL.txt"

[Messages]
; the first dialog is shown before a language is chosen, so its text is bilingual in both languages.
; (These are built-in messages, so they belong in [Messages], not in [CustomMessages].)
en.SelectLanguageTitle=Language / Język
en.SelectLanguageLabel=Choose the language of the installed application:%nWybierz język instalowanej aplikacji:
pl.SelectLanguageTitle=Language / Język
pl.SelectLanguageLabel=Choose the language of the installed application:%nWybierz język instalowanej aplikacji:

[CustomMessages]
en.MenuUpdateTools=update tools
en.MenuReadMe=read me
en.MenuUninstall=Uninstall
en.ReadMeFile=README.txt
en.MenuSources=licences and source code
en.AddingMenu=Adding Konwertexpol to the right-click menu...
pl.MenuUpdateTools=aktualizuj narzędzia
pl.MenuReadMe=instrukcja
pl.MenuUninstall=Odinstaluj
pl.ReadMeFile=README_PL.txt
pl.MenuSources=licencje i kod źródłowy
pl.AddingMenu=Dodawanie Konwertexpol do menu prawego przycisku myszy...

[Files]
Source: "payload\*"; DestDir: "{app}"; Excludes: "SOURCES.txt"; Flags: recursesubdirs createallsubdirs ignoreversion
; SOURCES.txt, licenses and sources are rewritten by "Update tools", so normal users need write access to them
Source: "payload\SOURCES.txt"; DestDir: "{app}"; Flags: ignoreversion; Permissions: users-modify

[Dirs]
; the tools folder must stay writable for normal users, otherwise "Update tools" and paths.txt would need administrator rights
Name: "{app}\tools"; Permissions: users-modify
Name: "{app}\licenses"; Permissions: users-modify
Name: "{app}\sources"; Permissions: users-modify

[Icons]
Name: "{autoprograms}\{#AppName} - {cm:MenuUpdateTools}"; Filename: "{app}\update_tools.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName} - {cm:MenuReadMe}"; Filename: "{app}\{cm:ReadMeFile}"
Name: "{autoprograms}\{#AppName} - {cm:MenuSources}"; Filename: "{app}\SOURCES.txt"
Name: "{autoprograms}\{cm:MenuUninstall} {#AppName}"; Filename: "{uninstallexe}"

[Run]
; an older per-user menu (from install.bat) would hide the machine-wide one - remove it first, as the normal user
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" uninstall-user"; Flags: runhidden runasoriginaluser waituntilterminated
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" install --machine"; Flags: runhidden waituntilterminated; StatusMsg: "{cm:AddingMenu}"
; lets Explorer show the menu also when more than 15 files are selected (per user; needs Explorer restart / sign-in)
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" multi-on"; Flags: runhidden runasoriginaluser waituntilterminated

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: """{app}\register.py"" uninstall"; Flags: runhidden; RunOnceId: "RemoveKonwertexpolMenu"

[UninstallDelete]
; also what was added after the installation (updated tools, caches)
Type: filesandordirs; Name: "{app}"

[Code]
// remember the language chosen in the first wizard page: the program (and its menu, written by register.py in [Run])
// reads it from {app}\language.txt. Written before the files are copied, so it is there when [Run] starts.
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
  begin
    ForceDirectories(ExpandConstant('{app}'));
    SaveStringToFile(ExpandConstant('{app}\language.txt'), ActiveLanguage + #13#10, False);
  end;
end;
