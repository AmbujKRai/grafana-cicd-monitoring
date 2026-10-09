<#
.SYNOPSIS
    Runs the pipeline's lint and test stages locally before pushing (shift-left).
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-Location $Root

$failed = @()
function Invoke-Check([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { $script:failed += $Name }
}

Invoke-Check 'ruff lint' { & $VenvPython -m ruff check . }
Invoke-Check 'ruff format' { & $VenvPython -m ruff format --check . }
Invoke-Check 'app tests + coverage gate' { & $VenvPython -m pytest --cov=app --cov-report=term }
Invoke-Check 'exporter tests' { Push-Location monitoring\exporter; & $VenvPython -m pytest tests -q; Pop-Location }
Invoke-Check 'dashboards up to date' {
    # regenerating must not change any committed dashboard JSON
    $dir = Join-Path $Root 'monitoring\grafana\dashboards'
    $before = Get-ChildItem $dir -Filter *.json | Get-FileHash | ForEach-Object Hash
    & $VenvPython scripts\build_dashboards.py | Out-Null
    $after = Get-ChildItem $dir -Filter *.json | Get-FileHash | ForEach-Object Hash
    $global:LASTEXITCODE = if (Compare-Object $before $after) { 1 } else { 0 }
}

if ($failed.Count -gt 0) {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'All checks passed - safe to push.' -ForegroundColor Green
