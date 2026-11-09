<#
  Makes LATEST\Konwertexpol_github_repo.zip from the committed files (git archive; the folder inside is "repo\").
  Commit your changes first - only what is committed ends up in the ZIP.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
New-Item -ItemType Directory -Force "LATEST" | Out-Null
$zip = Join-Path $root "LATEST\Konwertexpol_github_repo.zip"
git archive --format=zip --prefix=repo/ -o $zip HEAD
$h = Get-FileHash $zip -Algorithm SHA256
"{0}`n  SHA-256: {1}" -f $h.Path, $h.Hash.ToLower()
