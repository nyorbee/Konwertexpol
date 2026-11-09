<#
  Builds the installer (installer\output\Konwertexpol_Setup.exe) and copies it to LATEST\.
  Needs: Inno Setup 6 and Python 3 (py launcher) - see installer\README_BUILD.txt.
  Usage:  .\build.ps1            (asks for the language of the build messages)
          .\build.ps1 -Lang pl   (or en)
#>
param([ValidateSet("", "pl", "en")][string]$Lang = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Join-Path $root "installer")
& cmd /c "build_installer.bat $Lang"
$exe = Join-Path $root "installer\output\Konwertexpol_Setup.exe"
if (-not (Test-Path $exe)) { Write-Error "Konwertexpol_Setup.exe was not created."; exit 1 }
$dest = Join-Path $root "LATEST"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item $exe $dest -Force
$h = Get-FileHash (Join-Path $dest "Konwertexpol_Setup.exe") -Algorithm SHA256
"{0}`n  {1:N1} MB`n  SHA-256: {2}" -f $h.Path, ((Get-Item $h.Path).Length / 1MB), $h.Hash.ToLower()
