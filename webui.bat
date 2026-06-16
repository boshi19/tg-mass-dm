@echo off
chcp 936 >nul 2>&1
title tg-mass-dm WebUI v4.0-web
cd /d "%~dp0"

echo =======================================================
echo   tg-mass-dm WebUI v4.0-web
echo   URL: http://127.0.0.1:8000
echo =======================================================
echo.

if exist ".venv\Scripts\python.exe" (
    start "" "http://127.0.0.1:8000"
    ".venv\Scripts\python.exe" main.py
) else (
    start "" "http://127.0.0.1:8000"
    python main.py
)

echo.
pause
