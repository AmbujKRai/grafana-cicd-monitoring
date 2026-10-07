<#
.SYNOPSIS
    Starts the monitoring stack natively: exporter, Prometheus, Grafana and (optionally) Jenkins.

.DESCRIPTION
    Every component runs as a background process bound to 127.0.0.1. Logs go to .logs\,
    process ids to .run\ (used by stop-stack.ps1). Settings are read from .env.

.PARAMETER NoJenkins
    Do not start the local Jenkins server.

.PARAMETER UseGhToken
    Give the exporter the token of the logged-in GitHub CLI (gh auth token) to raise the
    API limit from 60 to 5000 requests/hour. Without it the exporter runs unauthenticated.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-stack.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoJenkins,
    [switch]$UseGhToken
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

if (-not (Test-Path $GrafanaHome) -or -not (Test-Path $VenvPython)) {
    throw 'Tools are missing - run scripts\setup-windows.ps1 first.'
}
New-Item -ItemType Directory -Force $DataDir, $LogDir, $RunDir | Out-Null
Import-DotEnv (Join-Path $Root '.env')

if (-not $env:GITHUB_REPOSITORY) { $env:GITHUB_REPOSITORY = Get-RepositorySlug }
if (-not $env:GF_SECURITY_ADMIN_PASSWORD) { $env:GF_SECURITY_ADMIN_PASSWORD = 'admin' }
if ($UseGhToken) {
    $env:GITHUB_TOKEN = (gh auth token 2>$null)
    if (-not $env:GITHUB_TOKEN) { throw 'gh auth token returned nothing - run "gh auth login" first.' }
}

function Start-Component([string]$Name, [string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory = $Root) {
    $service = $Services[$Name]
    if (Test-Port $service.Port) {
        Write-Host ("  {0,-11} already running on port {1}" -f $Name, $service.Port) -ForegroundColor Yellow
        return
    }
    $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") -RedirectStandardError (Join-Path $LogDir "$Name.log") `
        -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value $proc.Id
    Write-Host ("  {0,-11} started (pid {1})" -f $Name, $proc.Id)
}

Write-Host "Starting CI/CD monitoring stack for $($env:GITHUB_REPOSITORY)" -ForegroundColor Cyan

# 1. CI/CD metrics exporter (GitHub Actions -> Prometheus metrics, Grafana alert webhook)
$env:EXPORTER_BIND = '127.0.0.1'
$env:EXPORTER_DATA_DIR = Join-Path $DataDir 'exporter'
Start-Component exporter $VenvPython @('-m', 'cicd_exporter') (Join-Path $Root 'monitoring\exporter')

# 2. Prometheus
Start-Component prometheus (Join-Path $PrometheusHome 'prometheus.exe') @(
    ('--config.file=' + (Quote (Join-Path $Root 'monitoring\prometheus\prometheus.yml'))),
    ('--storage.tsdb.path=' + (Quote (Join-Path $DataDir 'prometheus'))),
    '--storage.tsdb.retention.time=30d',
    '--web.listen-address=127.0.0.1:9090',
    '--web.enable-lifecycle'
)

# 3. Grafana (provisioned from monitoring\grafana)
$env:GF_PATHS_DATA = Join-Path $DataDir 'grafana'
$env:GF_PATHS_LOGS = Join-Path $LogDir 'grafana'
$env:GF_PATHS_PLUGINS = Join-Path $DataDir 'grafana\plugins'
$env:GF_PATHS_PROVISIONING = Join-Path $Root 'monitoring\grafana\provisioning'
$env:GF_DASHBOARDS_DIR = Join-Path $Root 'monitoring\grafana\dashboards'
$env:GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH = Join-Path $env:GF_DASHBOARDS_DIR 'cicd-overview.json'
$env:PROMETHEUS_URL = 'http://localhost:9090'
$env:ALERT_WEBHOOK_URL = 'http://localhost:9200/alerts'
# Grafana does not create missing parent folders on Windows
New-Item -ItemType Directory -Force $env:GF_PATHS_DATA, $env:GF_PATHS_LOGS, $env:GF_PATHS_PLUGINS | Out-Null
Start-Component grafana (Join-Path $GrafanaHome 'bin\grafana.exe') @(
    'server',
    ('--homepath=' + (Quote $GrafanaHome)),
    ('--config=' + (Quote (Join-Path $Root 'monitoring\grafana\grafana.ini')))
)

# 4. Jenkins (configured as code from monitoring\jenkins\jenkins.yaml)
if (-not $NoJenkins) {
    $env:JENKINS_HOME = $JenkinsHome
    $env:CASC_JENKINS_CONFIG = Join-Path $Root 'monitoring\jenkins\jenkins.yaml'
    if (-not $env:JENKINS_REPO_URL) { $env:JENKINS_REPO_URL = "https://github.com/$($env:GITHUB_REPOSITORY).git" }
    Start-Component jenkins 'java' @(
        '-Djenkins.install.runSetupWizard=false',
        '-Djava.awt.headless=true',
        '-Xmx768m',
        '-jar', (Quote $JenkinsWar),
        '--httpPort=8081',
        '--httpListenAddress=127.0.0.1'
    )
}

Write-Host 'Waiting for health checks...'
$deadline = (Get-Date).AddSeconds(180)
$pending = [System.Collections.ArrayList]@($Services.Keys | Where-Object { -not ($NoJenkins -and $_ -eq 'jenkins') })
while ($pending.Count -gt 0 -and (Get-Date) -lt $deadline) {
    foreach ($name in @($pending)) {
        if (Test-Http $Services[$name].Health) { $pending.Remove($name) }
    }
    if ($pending.Count -gt 0) { Start-Sleep -Seconds 2 }
}

Write-Host ''
foreach ($name in $Services.Keys) {
    if ($NoJenkins -and $name -eq 'jenkins') { continue }
    $ok = -not $pending.Contains($name)
    $state = if ($ok) { 'UP  ' } else { 'DOWN' }
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1,-11} {2}" -f $state, $name, $Services[$name].Url) -ForegroundColor $color
}
if ($pending.Count -gt 0) {
    Write-Host "Some services did not become healthy - check the logs in .logs\" -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host 'Grafana: http://localhost:3000  (dashboards are public read-only; log in as admin to edit)' -ForegroundColor Green
