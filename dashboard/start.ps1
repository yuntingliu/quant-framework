$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ProjectPython = Join-Path $Root ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $ProjectPython) {
    $ProjectPython
} else {
    (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $Python) { $Python = "python" }

Write-Host "Starting AlphaLab Barebone backend on http://127.0.0.1:8000" -ForegroundColor Cyan
$backend = Start-Process -PassThru -WindowStyle Hidden $Python `
    -ArgumentList "-m", "uvicorn", "dashboard.backend.main:app", "--reload", "--port", "8000" `
    -WorkingDirectory $Root

Start-Sleep -Seconds 2

Write-Host "Starting Vite frontend on http://localhost:5173" -ForegroundColor Cyan
$frontendDir = Join-Path $Root "dashboard\frontend"
$frontend = Start-Process -PassThru -WindowStyle Hidden "npm" `
    -ArgumentList "run", "dev:web" `
    -WorkingDirectory $frontendDir

Start-Sleep -Seconds 4
Start-Process "http://localhost:5173"

Write-Host "Press Ctrl+C to stop both servers." -ForegroundColor Gray
try {
    Wait-Process -Id $backend.Id
} finally {
    Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue
}
