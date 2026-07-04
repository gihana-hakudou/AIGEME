@echo off
title AIGEME Setup
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=!SCRIPT_DIR!venv"
set "PYTHON=!VENV_DIR!\Scripts\python.exe"
set "BROWSER_DIR=!SCRIPT_DIR!.AIGEME\.browser"

echo ============================
echo    AIGEME Environment Setup
echo ============================
echo.

:: -- Check if existing venv is usable --
if exist "!PYTHON!" (
    echo [INFO] venv already exists at !VENV_DIR!, skipping Python check.
    goto :install_deps
)

:: -- Check Python --
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Python not found. Attempting automatic install via winget...
    echo.
    where winget >nul 2>&1
    if !errorlevel! equ 0 (
        winget install Python.Python.3.12 --silent --accept-package-agreements
        if !errorlevel! equ 0 (
            echo [OK] Python installed. Please close this window and re-run setup.bat.
        ) else (
            echo [ERROR] Automatic install failed.
            echo Please download Python 3.12+ manually from:
            echo   https://www.python.org/downloads/
            echo Make sure to check "Add Python to PATH" during installation.
        )
    ) else (
        echo [ERROR] winget not available. Please install Python 3.12+ manually:
        echo   https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during installation.
    )
    pause
    exit /b 1
)

:: -- Check Python version --
python --version 2>&1 | findstr "3.12 3.13" >nul
if %errorlevel% neq 0 (
    echo [WARN] Recommended Python 3.12+. Current version may not be compatible.
)

:: -- Create venv --
echo [INFO] Creating Python virtual environment...
python -m venv "!VENV_DIR!"
if !errorlevel! neq 0 (
    echo [ERROR] Failed to create venv.
    pause
    exit /b 1
)
echo [OK] venv created.

:install_deps

:: -- Install dependencies --
echo [INFO] Installing project dependencies...
"!PYTHON!" -m pip install --upgrade pip -q
"!PYTHON!" -m pip install -e "!SCRIPT_DIR!.[dev]" -q
if %errorlevel% neq 0 (
    echo [WARN] pip install failed, trying requirements.txt...
    if exist "!SCRIPT_DIR!requirements.txt" (
        "!PYTHON!" -m pip install -r "!SCRIPT_DIR!requirements.txt"
    )
)

:: -- Apply litellm Anthropic streaming fix --
echo [INFO] Applying litellm anthropic streaming patch...
"!PYTHON!" "!SCRIPT_DIR!scripts\patch_litellm.py"

:: -- Install Patchright Chromium to project local .AIGEME/.browser/ --
echo [INFO] Installing browser runtime (~150MB first time)...
if not exist "!BROWSER_DIR!" mkdir "!BROWSER_DIR!"

set "PLAYWRIGHT_BROWSERS_PATH=!BROWSER_DIR!"
"!PYTHON!" -m patchright install chromium
if %errorlevel% neq 0 (
    echo [WARN] Chromium install failed. The app will retry automatically on first launch.
)

echo.
echo ============================
echo    Setup complete!
echo    Run start.bat to launch
echo ============================
pause
