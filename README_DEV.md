# Konwertexpol - working folder

* Source (this is the repository): `converter.py`, `tools_manager.py`, `register.py`, `licensing.py`, `ffmpeg_libs.py`,
  `i18n.py`, `lang_pl.py`, `launch.py`, `icons\`, `installer\`, `screenshots\`, the READMEs and the licence files.
* `LATEST\` - the newest build (Setup.exe, ZIPs). Not in git.
* `archiwum\` - older ZIPs and installers. Not in git.

## Build and release
1. Change the code, test it (`python converter.py` needs the tools from `tools\`, see README.txt).
2. Raise `AppVersion` in `installer\Konwertexpol.iss` for a new release.
3. `git add -A` and `git commit -m "..."`.
4. `.\build.ps1` - builds `installer\output\Konwertexpol_Setup.exe` and copies it to `LATEST\` (needs Inno Setup 6 and Python 3).
   Afterwards look at `installer\payload\TO_DO_BY_HAND.txt`.
5. `.\hash.ps1` - SHA-256 for the release notes.
6. `git push`, then on GitHub: Releases -> new release (tag `vX.Y.Z`), attach `Konwertexpol_Setup.exe`, paste the SHA-256.

`.\make_repo_zip.ps1` makes a ZIP of the committed files (`LATEST\Konwertexpol_github_repo.zip`).
