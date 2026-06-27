@echo off
title AIGEME

setlocal enabledelayedexpansion
set "SCRIPT_DIR=%~dp0"
set "PYTHON=!SCRIPT_DIR!venv\Scripts\python.exe"

if not exist "!PYTHON!" (
    echo [INFO] First run detected. Setting up environment...
    call "!SCRIPT_DIR!setup.bat"
    if not exist "!PYTHON!" (
        echo [ERROR] Environment setup failed. Please run setup.bat manually.
        pause
        exit /b 1
    )
)

echo.
echo ============================
echo    AIGEME is starting...
echo ============================
echo.
echo [INFO] Using bundled runtime, no installation required
echo.

start "" /B "!PYTHON!" "!SCRIPT_DIR!main.py"

:wait_loop
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "try{$r=curl.exe -s -o nul -w '%%{http_code}' http://127.0.0.1:8765/ 2>$null;if($r.length -gt 0 -and $r -ge 200 -and $r -lt 400){exit 0}}catch{};exit 1" >nul 2>&1
if %errorlevel% neq 0 goto wait_loop

echo.
echo [OK] Server is ready!
echo.
echo [INFO] The frontend will open automatically.
echo [INFO] Press Ctrl+C to stop the server

:hold
timeout /t 86400 /nobreak >nul
goto hold