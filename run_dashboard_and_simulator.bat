@echo off
setlocal ENABLEDELAYEDEXPANSION

REM ==========================================================
REM run_all.bat
REM - Ensures venv exists
REM - Installs/updates requirements
REM - Runs dashboard + simulator in two terminals
REM ==========================================================

REM Go to project root (where this .bat lives)
cd /d "%~dp0"

set "VENV_DIR=venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "ACTIVATE_BAT=%VENV_DIR%\Scripts\activate.bat"

echo.
echo [1/4] Checking virtual environment...

if not exist "%PYTHON_EXE%" (
    echo Creating venv in "%VENV_DIR%" ...
    py -3 -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Make sure Python is installed and "py" launcher exists.
        pause
        exit /b 1
    )
)

echo.
echo [2/4] Activating venv...
call "%ACTIVATE_BAT%"
if errorlevel 1 (
    echo ERROR: Failed to activate venv.
    pause
    exit /b 1
)

echo.
echo [3/4] Updating pip + installing/upgrading requirements...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo ERROR: Failed to upgrade pip/setuptools/wheel.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo ERROR: requirements.txt not found in project root.
    pause
    exit /b 1
)

python -m pip install --upgrade -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install requirements.
    pause
    exit /b 1
)

echo.
echo [4/4] Starting Dashboard and Simulator in two terminals...

REM Optional: create logs folder if your app expects it
if not exist "logs" mkdir "logs"

REM Terminal 1: Dashboard
start "Dashboard" cmd /k "cd /d "%CD%" && call "%ACTIVATE_BAT%" && python -m src.dashboard.main"

REM Small delay so dashboard starts first
timeout /t 3 /nobreak >nul 

REM Terminal 2: Simulator
start "Simulator" cmd /k "cd /d "%CD%" && call "%ACTIVATE_BAT%" && python -m src.simulator.main"

echo.
echo Done. Two terminals opened: Dashboard + Simulator.
echo Close the terminals to stop them.
echo.
exit /b 0
