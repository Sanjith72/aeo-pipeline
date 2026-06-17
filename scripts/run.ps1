<#
.SYNOPSIS
  One command to run the whole AEO Studio stack locally - backend API + web UI.

.DESCRIPTION
  Starts the FastAPI backend (aeo serve, :8000) and the Next.js frontend (:3000) each in
  its own window, waits for them to come up, and opens the browser. No Docker required -
  uses the project's existing .venv and web/node_modules. Postgres only matters for the
  Deep-audit feature; everything else (plan / quick analysis / deliverables) runs without it.

.EXAMPLE
  .\scripts\run.ps1            # LLM on (the default); slower if pointed at local Ollama
  .\scripts\run.ps1 -NoLlm     # deterministic + fast (skips the LLM)

  Stop it by closing the two windows, or run .\scripts\stop.ps1
#>
[CmdletBinding()]
param(
  [switch]$NoLlm,
  [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$web = Join-Path $root "web"

if (-not (Test-Path $py)) {
  Write-Error "No virtualenv at $py. First-time setup:  python -m venv .venv ; .\.venv\Scripts\pip install -e `".[api]`""
}
if (-not (Test-Path (Join-Path $web "node_modules"))) {
  Write-Warning "web\node_modules missing - running 'npm install' in web\ first..."
  Push-Location $web; npm install; Pop-Location
}

# Env vars set here are inherited by the child windows started below.
$env:PYTHONPATH = Join-Path $root "src"
if ($NoLlm) { $env:AEO__LLM__ENABLED = "false" }

# Backend API + frontend web, each in its own window (so you see logs / can Ctrl+C).
Start-Process powershell -WorkingDirectory $root `
  -ArgumentList "-NoExit", "-Command", "& '$py' -m aeo.cli serve --host 127.0.0.1 --port 8000"
Start-Process powershell -WorkingDirectory $web `
  -ArgumentList "-NoExit", "-Command", "npm run dev"

Write-Host "Starting AEO Studio..." -ForegroundColor Green
Write-Host "  API : http://localhost:8000   (interactive docs at /docs)"
Write-Host "  Web : http://localhost:3000"
if ($NoLlm) { Write-Host "  LLM : off (deterministic)" }
else { Write-Host "  LLM : on  (default Ollama is slow on CPU - set AEO__LLM__PROVIDER=cloud for speed)" }

if (-not $NoBrowser) {
  $up = $false
  foreach ($i in 1..60) {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://localhost:3000" | Out-Null; $up = $true; break }
    catch { Start-Sleep -Milliseconds 1000 }
  }
  if ($up) { Start-Process "http://localhost:3000"; Write-Host "Opened http://localhost:3000" -ForegroundColor Green }
  else { Write-Host "Web is still starting - open http://localhost:3000 manually in a moment." -ForegroundColor Yellow }
}
