<#
.SYNOPSIS
    Prints the alert notifications Grafana delivered to the exporter webhook (.data\exporter\alerts.log).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\show-alerts.ps1 -Last 10
#>
[CmdletBinding()]
param(
    [int]$Last = 20
)

. (Join-Path $PSScriptRoot 'common.ps1')

$log = Join-Path $DataDir 'exporter\alerts.log'
if (-not (Test-Path $log)) {
    Write-Host 'No alert notifications received yet.'
    return
}

foreach ($line in Get-Content $log -Tail $Last) {
    if (-not $line.Trim()) { continue }
    $alert = $line | ConvertFrom-Json
    $when = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]($alert.received_at * 1000)).LocalDateTime
    $color = if ($alert.status -eq 'firing') { 'Red' } else { 'Green' }
    Write-Host ("{0:yyyy-MM-dd HH:mm:ss}  ALERT {1,-9} {2,-42} [{3}] {4}" -f `
            $when, $alert.status.ToUpper(), $alert.alertname, $alert.severity, $alert.summary) -ForegroundColor $color
}
