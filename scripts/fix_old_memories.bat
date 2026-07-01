@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=!SCRIPT_DIR!..\venv\Scripts\python.exe"

if not exist "!VENV_PYTHON!" (
    echo [ERROR] venv not found at !VENV_PYTHON!
    echo Run setup.bat first to create the virtual environment.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Fix old memory files
echo   - Strip type field padding
echo   - Add title / importance to frontmatter
echo   Backup saved to bak/ directory
echo ============================================
echo.

"!VENV_PYTHON!" "!SCRIPT_DIR!fix_old_memories.py" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Script exited with error code %ERRORLEVEL%
    pause
)
