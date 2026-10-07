# Shared settings for the setup/start/stop/status scripts (dot-sourced).

$Root = Split-Path -Parent $PSScriptRoot
$Tools = Join-Path $Root '.tools'
$Downloads = Join-Path $Tools 'downloads'
$DataDir = Join-Path $Root '.data'
$LogDir = Join-Path $Root '.logs'
$RunDir = Join-Path $Root '.run'

# Pinned releases and their official SHA-256 checksums
$Releases = @{
    Prometheus    = @{
        Version = '3.15.0'
        File    = 'prometheus-3.15.0.windows-amd64.zip'
        Url     = 'https://github.com/prometheus/prometheus/releases/download/v3.15.0/prometheus-3.15.0.windows-amd64.zip'
        Sha256  = '5d333b385557d9adc2ff015d13da9809baccc52fb800a82d1e5c94b79258f87e'
    }
    Grafana       = @{
        Version = '13.2.3'
        File    = 'grafana-13.2.3.windows-amd64.zip'
        Url     = 'https://dl.grafana.com/oss/release/grafana-13.2.3.windows-amd64.zip'
        Sha256  = 'a208dc3a0a1c237f275490cc9b9219c89c22bc3a0e62ba0fe5642b6e93cb6b7f'
    }
    Jenkins       = @{
        Version = '2.580.1'
        File    = 'jenkins-2.580.1.war'
        Url     = 'https://get.jenkins.io/war-stable/2.580.1/jenkins.war'
        Sha256  = '393bf2476352dd726519fd1f92ce66eac7d23c0936e6967420b717060c4c40d0'
    }
    PluginManager = @{
        Version = '2.15.0'
        File    = 'jenkins-plugin-manager-2.15.0.jar'
        Url     = 'https://github.com/jenkinsci/plugin-installation-manager-tool/releases/download/2.15.0/jenkins-plugin-manager-2.15.0.jar'
        Sha256  = 'a86853ec2e2933f37a4b471ba65099b61e03c87a80c2ef8fe2315eb135672d43'
    }
}

$PrometheusHome = Join-Path $Tools 'prometheus-3.15.0.windows-amd64'
$GrafanaHome = Join-Path $Tools 'grafana-13.2.3'
$JenkinsWar = Join-Path $Downloads $Releases.Jenkins.File
$JenkinsHome = Join-Path $Tools 'jenkins-home'
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'

$Services = [ordered]@{
    exporter   = @{ Port = 9200; Health = 'http://127.0.0.1:9200/healthz'; Url = 'http://localhost:9200/metrics'; Process = 'python' }
    prometheus = @{ Port = 9090; Health = 'http://127.0.0.1:9090/-/ready'; Url = 'http://localhost:9090'; Process = 'prometheus' }
    grafana    = @{ Port = 3000; Health = 'http://127.0.0.1:3000/api/health'; Url = 'http://localhost:3000'; Process = 'grafana' }
    jenkins    = @{ Port = 8081; Health = 'http://127.0.0.1:8081/login'; Url = 'http://localhost:8081'; Process = 'java' }
}

function Quote([string]$Value) {
    if ($Value -match '\s') { return '"' + $Value + '"' } else { return $Value }
}

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Test-Http([string]$Url) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return $resp.StatusCode -lt 400
    } catch {
        return $false
    }
}

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    foreach ($line in Get-Content $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name, $value = $Matches[1], $Matches[2].Trim('"').Trim("'")
            if ($value -ne '') { Set-Item -Path "env:$name" -Value $value }
        }
    }
}

function Get-RepositorySlug {
    $remote = git -C $Root remote get-url origin 2>$null
    if ($remote -match 'github\.com[:/](.+?)(\.git)?$') { return $Matches[1] }
    return $null
}
