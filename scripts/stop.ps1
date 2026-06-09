<#
.SYNOPSIS
  Stop the locally-running AEO Studio stack (whatever is bound to the API + web ports).
.DESCRIPTION
  Frees ports 8000 (API) and 3000 (web) by stopping the processes listening on them.
  Closing the two windows that run.ps1 opened does the same thing.
#>
[CmdletBinding()]
param(
  [int[]]$Ports = @(8000, 3000)
)

foreach ($port in $Ports) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if (-not $conns) { Write-Host "port $port : nothing listening"; continue }
  foreach ($procId in ($conns.OwningProcess | Select-Object -Unique)) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    Write-Host "port $port : stopping pid=$procId ($($proc.ProcessName))"
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
  }
}
Write-Host "AEO Studio stopped." -ForegroundColor Green
