<#
.SYNOPSIS
    Installs a corporate root CA certificate into both Docker build contexts.

.DESCRIPTION
    Copies a single .crt file into infra/certs/ (backend build context) and
    frontend/certs/ (frontend build context) so all images in this repo
    trust it. Run once after exporting your corporate CA, then rebuild
    images with `docker compose build`.

    If no source path is given, the script looks for the Bajaj root CA in
    the Windows certificate store (LocalMachine\Root) by Subject CN match
    and exports it automatically.

.PARAMETER SourcePath
    Path to a PEM/DER .crt file to install. If omitted, exports the first
    matching cert from Windows cert store using -SubjectMatch.

.PARAMETER SubjectMatch
    Subject substring used when auto-exporting from the Windows cert store.
    Default: "Bajaj".

.PARAMETER OutName
    Filename to use under both certs/ directories. Default: "corp-root-ca.crt".

.EXAMPLE
    .\scripts\install-corp-ca.ps1 C:\path\to\BAJAJ-ROOT-CA.crt

.EXAMPLE
    .\scripts\install-corp-ca.ps1 -SubjectMatch "Bajaj"
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$SourcePath,

    [string]$SubjectMatch = "Bajaj",

    [string]$OutName = "corp-root-ca.crt"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $repoRoot "infra\certs"
$frontendDir = Join-Path $repoRoot "frontend\certs"

foreach ($d in @($backendDir, $frontendDir)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d | Out-Null
    }
}

if (-not $SourcePath) {
    Write-Host "No source path given - searching Windows cert store for subject matching '$SubjectMatch'..."
    $cert = Get-ChildItem -Path Cert:\LocalMachine\Root, Cert:\CurrentUser\Root |
            Where-Object { $_.Subject -match $SubjectMatch } |
            Select-Object -First 1
    if (-not $cert) {
        throw "No certificate found in Cert:\LocalMachine\Root or Cert:\CurrentUser\Root with subject matching '$SubjectMatch'. Pass -SourcePath explicitly or open certmgr.msc to identify the right CN."
    }
    Write-Host "Found: $($cert.Subject)"
    $tmpPath = Join-Path ([System.IO.Path]::GetTempPath()) "$OutName"
    $pem = "-----BEGIN CERTIFICATE-----`n"
    $pem += [System.Convert]::ToBase64String($cert.RawData, [System.Base64FormattingOptions]::InsertLineBreaks)
    $pem += "`n-----END CERTIFICATE-----`n"
    [System.IO.File]::WriteAllText($tmpPath, $pem)
    $SourcePath = $tmpPath
}

if (-not (Test-Path $SourcePath)) {
    throw "Source cert not found: $SourcePath"
}

$dst1 = Join-Path $backendDir $OutName
$dst2 = Join-Path $frontendDir $OutName

Copy-Item -Path $SourcePath -Destination $dst1 -Force
Copy-Item -Path $SourcePath -Destination $dst2 -Force

Write-Host "Installed corporate CA:"
Write-Host "  -> $dst1"
Write-Host "  -> $dst2"
Write-Host ""
Write-Host "Next: docker compose build"
