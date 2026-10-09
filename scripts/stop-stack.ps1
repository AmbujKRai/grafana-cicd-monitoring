<#
.SYNOPSIS
    Stops the processes started by start-stack.ps1 (data in .data\ is kept).
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot 'common.ps1')

foreach ($name in $Services.Keys) {
    $pidFile = Join-Path $RunDir "$name.pid"
    if (-not (Test-Path $pidFile)) { continue }
    $id = [int](Get-Content $pidFile)
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    # Only stop the process if the pid still belongs to the component we started
    if ($proc -and $proc.ProcessName -like "$($Services[$name].Process)*") {
        # /T stops child processes too (the venv python.exe is a launcher that starts the real interpreter)
        taskkill.exe /PID $id /T /F | Out-Null
        Write-Host ("  stopped {0,-11} (pid {1})" -f $name, $id)
    } else {
        Write-Host ("  {0,-11} was not running" -f $name)
    }
    Remove-Item $pidFile
}
