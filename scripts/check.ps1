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
Invoke-Check 'dashboards up to date' { & $VenvPython scripts\build_dashboards.py | Out-Null; git diff --quiet -- monitoring/grafana/dashboards }

if ($failed.Count -gt 0) {
    Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host 'All checks passed - safe to push.' -ForegroundColor Green
