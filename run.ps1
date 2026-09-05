# Namma Transit Agent — one-shot launcher (PowerShell)
# Starts ngrok tunnel + uvicorn. Twilio sandbox webhook:
#   https://registrar-coronary-reprogram.ngrok-free.dev/whatsapp  (stable URL)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creating venv (Python 3.13)..." -ForegroundColor Cyan
    py -3.13 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
    .\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt --quiet
}

# Fresh tunnel on the stable domain; kill any stale instance first.
Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1
Start-Process -WindowStyle Minimized ngrok -ArgumentList "http","8000","--url=https://registrar-coronary-reprogram.ngrok-free.dev"

Write-Host "Tunnel: https://registrar-coronary-reprogram.ngrok-free.dev" -ForegroundColor Green
Write-Host "Starting FastAPI on :8000 ..." -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
