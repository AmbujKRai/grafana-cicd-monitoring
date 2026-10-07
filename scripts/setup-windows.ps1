<#
.SYNOPSIS
    One-time setup of the CI/CD monitoring stack on Windows (no Docker required).

.DESCRIPTION
    Downloads pinned releases of Prometheus, Grafana and Jenkins into .tools\, verifies their
    SHA-256 checksums, installs the Jenkins plugins from monitoring\jenkins\plugins.txt and
    creates a Python virtual environment (.venv) for the exporter and the test tools.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipJenkins
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'common.ps1')

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Assert-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name not found on PATH. $Hint" }
}

function Get-VerifiedFile($Release) {
    $target = Join-Path $Downloads $Release.File
    if (Test-Path $target) {
        if ((Get-FileHash $target -Algorithm SHA256).Hash -eq $Release.Sha256) {
            Write-Host "    $($Release.File) already downloaded"
            return $target
        }
        Remove-Item $target
    }
    Write-Host "    downloading $($Release.Url)"
    curl.exe -sSfL --retry 3 -o $target $Release.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($Release.Url)" }
    $hash = (Get-FileHash $target -Algorithm SHA256).Hash
    if ($hash -ne $Release.Sha256) {
        Remove-Item $target
        throw "Checksum mismatch for $($Release.File): expected $($Release.Sha256), got $hash"
    }
    Write-Host "    verified SHA-256 $hash"
    return $target
}

Write-Step 'Checking prerequisites'
Assert-Command python 'Install Python 3.11+ from https://www.python.org/downloads/'
Assert-Command git 'Install Git for Windows from https://git-scm.com/'
if (-not $SkipJenkins) { Assert-Command java 'Install a Java 21 JDK (e.g. Eclipse Temurin) or rerun with -SkipJenkins' }
New-Item -ItemType Directory -Force $Downloads, $DataDir, $LogDir, $RunDir | Out-Null

Write-Step 'Downloading pinned releases'
$promZip = Get-VerifiedFile $Releases.Prometheus
$grafanaZip = Get-VerifiedFile $Releases.Grafana
if (-not $SkipJenkins) {
    Get-VerifiedFile $Releases.Jenkins | Out-Null
    $pluginManager = Get-VerifiedFile $Releases.PluginManager
}

Write-Step 'Extracting Prometheus and Grafana'
foreach ($pair in @(@($promZip, $PrometheusHome), @($grafanaZip, $GrafanaHome))) {
    if (Test-Path $pair[1]) { Write-Host "    $(Split-Path $pair[1] -Leaf) already extracted"; continue }
    tar.exe -xf $pair[0] -C $Tools
    if ($LASTEXITCODE -ne 0) { throw "Could not extract $($pair[0])" }
}

if (-not $SkipJenkins) {
    Write-Step 'Installing Jenkins plugins'
    $pluginDir = Join-Path $JenkinsHome 'plugins'
    New-Item -ItemType Directory -Force $pluginDir | Out-Null
    & java -jar $pluginManager --war $JenkinsWar --plugin-download-directory $pluginDir `
        --plugin-file (Join-Path $Root 'monitoring\jenkins\plugins.txt') 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Jenkins plugin installation failed' }
    Write-Host "    $((Get-ChildItem $pluginDir -Filter *.jpi).Count) plugins installed (incl. dependencies)"
}

Write-Step 'Creating Python virtual environment (.venv)'
if (-not (Test-Path $VenvPython)) { python -m venv (Join-Path $Root '.venv') }
& $VenvPython -m pip install --quiet --disable-pip-version-check `
    -r (Join-Path $Root 'requirements-dev.txt') -r (Join-Path $Root 'monitoring\exporter\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

$envFile = Join-Path $Root '.env'
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root '.env.example') $envFile
    Write-Host '    created .env from .env.example'
}

Write-Host ''
Write-Host 'Setup complete. Start the stack with:' -ForegroundColor Green
Write-Host '    powershell -ExecutionPolicy Bypass -File scripts\start-stack.ps1'
