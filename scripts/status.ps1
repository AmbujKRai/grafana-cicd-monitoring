<#
.SYNOPSIS
    Health report for the monitoring stack: services, Prometheus targets and Grafana provisioning.
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'common.ps1')

Write-Host 'Services' -ForegroundColor Cyan
foreach ($name in $Services.Keys) {
    $ok = Test-Http $Services[$name].Health
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1,-11} {2}" -f $(if ($ok) { 'UP  ' } else { 'DOWN' }), $name, $Services[$name].Url) -ForegroundColor $color
}

try {
    Write-Host 'Prometheus scrape targets' -ForegroundColor Cyan
    $targets = (Invoke-RestMethod 'http://127.0.0.1:9090/api/v1/targets').data.activeTargets
    foreach ($t in $targets) {
        $color = if ($t.health -eq 'up') { 'Green' } else { 'Red' }
        Write-Host ("  {0,-14} {1,-5} last scrape {2:N3}s  {3}" -f $t.labels.job, $t.health, $t.lastScrapeDuration, $t.lastError) -ForegroundColor $color
    }
} catch { Write-Host '  Prometheus not reachable' -ForegroundColor Red }

try {
    Write-Host 'Grafana provisioning' -ForegroundColor Cyan
    $dash = Invoke-RestMethod 'http://127.0.0.1:3000/api/search?type=dash-db'
    Write-Host "  dashboards : $($dash.Count) ($(($dash | ForEach-Object title) -join ', '))"
    $rules = Invoke-RestMethod 'http://127.0.0.1:3000/api/prometheus/grafana/api/v1/rules'
    $all = @($rules.data.groups | ForEach-Object { $_.rules })
    $firing = @($all | Where-Object { $_.state -eq 'firing' })
    Write-Host "  alert rules: $($all.Count) ($($firing.Count) firing)"
    foreach ($r in $firing) { Write-Host "    FIRING $($r.name)" -ForegroundColor Red }
} catch { Write-Host '  Grafana not reachable' -ForegroundColor Red }

try {
    $h = Invoke-RestMethod 'http://127.0.0.1:9200/healthz'
    Write-Host 'Exporter' -ForegroundColor Cyan
    $last = if ($h.poller.last_success) { [DateTimeOffset]::FromUnixTimeSeconds([int64]$h.poller.last_success).LocalDateTime } else { 'never' }
    Write-Host "  repository : $($h.repository) (authenticated: $($h.authenticated))"
    Write-Host "  tracked    : $($h.tracked_runs) runs, last poll $last, API quota left $($h.rate_limit.remaining)/$($h.rate_limit.limit)"
} catch { }
