@echo off
echo === AlphaLab Barebone Frontend Setup ===
call npm install
if errorlevel 1 exit /b 1
call npm run build
