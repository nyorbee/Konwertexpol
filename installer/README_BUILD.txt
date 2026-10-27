BUILDING THE INSTALLER (Konwertexpol_Setup.exe)
===============================================
Most people do not need this: install.bat in the main folder is enough. Build the installer only if you want a
single Setup.exe with everything inside (program, private Python, all tools) that installs into Program Files.

WHAT YOU NEED (on the PC that builds it)
  1. Windows 10/11 and an internet connection (the tools are downloaded during the build).
  2. Python 3, 64-bit, the normal one from https://www.python.org/downloads/ (NOT the Microsoft Store version),
     with "tcl/tk and IDLE" ticked (it is by default).
  3. Inno Setup 6 (free): https://jrsoftware.org/isdl.php
  4. Optional: Ghostscript and Tesseract OCR installed on this PC. If they are, they are COPIED into the package.
     If they are not, their installers are run (confirm the UAC prompts).

LANGUAGE
  Both build_installer.bat and the resulting Setup.exe ask for the language (English / Polski) first. For the build script it
  only decides the language of its own messages (build_installer.bat en|pl skips the question). For the Setup.exe it
  decides the wizard language AND the language of the installed program and its right-click menu (saved in
  language.txt). The Polish wizard texts come from Inno Setup's Polish.isl; if your Inno Setup lacks it, put Polish.isl
  next to Konwertexpol.iss (the build then stops with a clear error).

HOW
  1. Download / clone the repository and unzip it.
  2. Run installer\build_installer.bat and wait (it can take several minutes).
  3. The result is installer\output\Konwertexpol_Setup.exe.

The script (1) copies the program, (2) copies the (cleaned) Python of the build PC as the private Python,
(3) downloads all tools into the package (so the installed program works offline), (4) runs make_compliance.py (licence
texts, SOURCES.txt and the source-code package), (5) builds the installer.

WHAT THE INSTALLER DOES ON THE TARGET PC
  * installs to C:\Program Files\Konwertexpol (administrator rights; one UAC prompt) - program, private Python, tools
  * makes the "tools" folder writable for normal users, so "Update tools" and tools\paths.txt work without admin
    (trade-off: on a PC shared with other users, they could replace the programs in that folder - fine for a personal PC)
  * registers the right-click menu for ALL users of the PC (HKLM) and removes an older per-user menu from install.bat
  * raises Explorer's multi-selection limit (15 -> 1000) for the installing user
  * Start menu: "update tools", "read me", "Uninstall"; Settings -> Apps lists Konwertexpol (uninstall removes everything)
  If the PC already has the portable (install.bat) version, run its uninstall.bat first so the menu is not doubled.

GOOD TO KNOW
  * The installer is not code-signed, so Windows SmartScreen shows "unknown publisher" (More info -> Run anyway).
  * LICENCES - WHAT YOU MUST DO WHEN YOU PUBLISH THE SETUP.EXE (it contains ffmpeg, Pandoc, Poppler - GPL, Ghostscript and
    PyMuPDF - AGPL, and others):
      1. The source archives of the GPL / AGPL / LGPL / MPL components are put into payload\sources and are therefore INSIDE
         Konwertexpol_Setup.exe; the installer puts them into Program Files\Konwertexpol\sources. No separate file is published.
      2. Read output\TO_DO_BY_HAND.txt (the same list is printed at the end of the build; the file exists only if
         something is left to do). Whatever could not be downloaded (e.g. the external libraries inside the FFmpeg and
         Poppler Windows builds) has to be put into payload\sources by hand, then compile the installer again
         (ISCC). Packages whose licence is missing from their metadata are listed there too.
      3. Rebuild whenever the bundled tools change: payload\sources must match the binaries in the Setup.exe.
         'Update tools' on the user's computer replaces licenses\<tool>, sources and the "updated" section of SOURCES.txt for the updated tools itself.
      4. The installer already shows LICENSE (MIT) and a notice page, and installs SOURCES.txt, the 'licenses' folder and
         THIRD_PARTY_NOTICES.md next to the program.
  * If a step fails, open an Issue and paste the messages printed by the script.
