<#
Exports the two corp CAs identified in the Windows trust store
(BajajLife-Root-CA and Cisco Umbrella Root CA) into both build contexts.
#>
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $repoRoot "infra\certs"
$frontendDir = Join-Path $repoRoot "frontend\certs"

foreach ($d in @($backendDir, $frontendDir)) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Path $d | Out-Null
    }
}

$certs = Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root |
    Where-Object { $_.Subject -match 'BajajLife-Root-CA|Cisco Umbrella' } |
    Sort-Object Thumbprint -Unique

if (-not $certs) {
    throw "No matching certs found. Open certmgr.msc to confirm."
}

$bundleParts = @()
foreach ($cert in $certs) {
    $cn = ($cert.Subject -split ',')[0] -replace 'CN=',''
    $slug = $cn -replace '[^a-zA-Z0-9]','-'
    $name = "$slug.crt"
    $pem = "-----BEGIN CERTIFICATE-----`n"
    $pem += [System.Convert]::ToBase64String($cert.RawData, [System.Base64FormattingOptions]::InsertLineBreaks)
    $pem += "`n-----END CERTIFICATE-----`n"
    $bundleParts += $pem

    $dst1 = Join-Path $backendDir $name
    $dst2 = Join-Path $frontendDir $name
    [System.IO.File]::WriteAllText($dst1, $pem)
    [System.IO.File]::WriteAllText($dst2, $pem)
    Write-Host "Installed $name"
    Write-Host "  Subject:    $($cert.Subject)"
    Write-Host "  Thumbprint: $($cert.Thumbprint)"
}

# Backend Dockerfiles also reference a consolidated bundle at this path.
$bundle = ($bundleParts -join "")
[System.IO.File]::WriteAllText((Join-Path $backendDir "bajaj-root.pem"), $bundle)
[System.IO.File]::WriteAllText((Join-Path $frontendDir "bajaj-root.pem"), $bundle)
Write-Host "Installed bajaj-root.pem (bundle of $($certs.Count) certs)"
