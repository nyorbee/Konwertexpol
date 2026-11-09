<# SHA-256 of the files in LATEST\ (for the release notes). #>
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Get-ChildItem (Join-Path $root "LATEST") -File | ForEach-Object {
    $h = Get-FileHash $_.FullName -Algorithm SHA256
    "{0}  {1}" -f $h.Hash.ToLower(), $_.Name
}
