@echo off
rem Try presets live with the arrow keys, through your normal speakers/headphones.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
.venv\Scripts\python live_flip.py %*
pause
