@echo off
rem Voice changer -> VB-Cable, for Discord/OBS/games. Extra arguments are passed through,
rem e.g.  run.bat --preset q1
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
.venv\Scripts\python seven_voice.py --out "CABLE Input" %*
pause
