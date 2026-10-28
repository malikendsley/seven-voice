@echo off
rem One-time setup: creates .venv and installs the dependencies.
cd /d "%~dp0"
python --version >nul 2>&1 || (echo Python was not found. Install Python 3.10 from python.org and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv\Scripts\python.exe python -m venv .venv || (echo Could not create .venv & pause & exit /b 1)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt || (echo Install failed. & pause & exit /b 1)
echo.
echo Setup done. Double-click run.bat to start, or presets.bat to try presets.
pause
