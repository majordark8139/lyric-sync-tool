@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo             Lyric Sync Tool (Windows)
echo ===================================================

set SCRIPT_DIR=%~dp0
set VENV_DIR=%SCRIPT_DIR%.venv
set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe

:: Detect Python command
set PYTHON_CMD=
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
) else (
    where py >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PYTHON_CMD=py -3
    )
)

if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python 3.10+ is required but was not found in PATH.
    echo Please download and install Python from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Auto-create virtual environment if missing
if not exist "%VENV_PYTHON%" (
    echo [INFO] First run detected. Creating virtual environment in %VENV_DIR%...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )

    echo [INFO] Installing required dependencies (PySide6, mutagen, requests)...
    "%VENV_PYTHON%" -m pip install --upgrade pip
    "%VENV_PYTHON%" -m pip install -r "%SCRIPT_DIR%requirements.txt"
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install dependencies from requirements.txt.
        pause
        exit /b 1
    )
    echo [INFO] Environment setup completed successfully.
    echo.
)

:: Launch the GUI application
set PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%
"%VENV_PYTHON%" "%SCRIPT_DIR%gui\app.py" %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [INFO] Application exited with code %ERRORLEVEL%.
    pause
)
