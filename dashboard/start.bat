@echo off
set ROOT=%~dp0..
cd /d "%ROOT%"

echo Starting AlphaLab Barebone backend on http://127.0.0.1:8000
start "AlphaLab Backend" cmd /k "python -m uvicorn dashboard.backend.main:app --reload --port 8000"

timeout /t 2 /nobreak >nul

echo Starting Vite frontend on http://localhost:5173
cd /d "%ROOT%\dashboard\frontend"
start "AlphaLab Frontend" cmd /k "npm run dev:web"

timeout /t 4 /nobreak >nul
start http://localhost:5173
