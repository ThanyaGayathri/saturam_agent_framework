@echo off
REM ###########################################################################
REM # EGPT Development Setup (Windows)
REM # - Create a virtual environment and install libraries in editable mode.
REM # - Please deactivate the existing virtual environment before running.
REM # Usage: scripts\dev_setup.bat
REM ###########################################################################

SETLOCAL ENABLEDELAYEDEXPANSION

REM Get current directory
SET "CURR_DIR=%~dp0"
SET "REPO_ROOT=%CURR_DIR%\.."
SET "AGNO_DIR=%REPO_ROOT%\libs\agno"
SET "EGPTCTL_DIR=%REPO_ROOT%\libs\egptctl"
SET "AGNO_INFRA_DIR=%REPO_ROOT%\libs\agno_infra"
SET "VENV_DIR=%REPO_ROOT%\.venv"

REM Function to print headings
CALL :print_heading "Development setup..."

CALL :print_heading "Removing virtual env"
ECHO [INFO] Removing %VENV_DIR%
rmdir /s /q "%VENV_DIR%" 2>nul

CALL :print_heading "Creating virtual env"
ECHO [INFO] Creating virtual environment in %VENV_DIR%
python -m venv "%VENV_DIR%"
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

REM Activate virtual environment
CALL :activate_venv
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

CALL :print_heading "Installing egptctl and egpt in editable mode with test dependencies"
pip install -e "%EGPTCTL_DIR%" -e "%AGNO_DIR%[tests]"
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

CALL :print_heading "Installing egpt-os"
ECHO [INFO] Installing dependencies from %AGNO_INFRA_DIR%\requirements.txt
pip install -r "%AGNO_INFRA_DIR%\requirements.txt"
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

CALL :print_heading "Installing egpt-os in editable mode with dev dependencies"
pip install -e "%AGNO_INFRA_DIR%[dev]"
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

CALL :print_heading "pip list"
pip list
IF ERRORLEVEL 1 EXIT /B %ERRORLEVEL%

CALL :print_heading "Development setup complete"
ECHO.
ECHO Activate venv using: .\.venv\Scripts\activate
ECHO.
EXIT /B

REM Function to print headings
:print_heading
ECHO.
ECHO ##################################################
ECHO # %1
ECHO ##################################################
ECHO.
EXIT /B

REM Function to activate virtual environment
:activate_venv
ECHO [INFO] Activating virtual environment...
CALL "%VENV_DIR%\Scripts\activate"
EXIT /B
