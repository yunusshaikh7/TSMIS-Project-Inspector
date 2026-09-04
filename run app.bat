@echo off
cd /d "%~dp0"
REM Run the desktop app in dev, using your global Python + the packages installed
REM by "setup (one time).bat" (pywebview, openpyxl). The packaged windowed .exe
REM (no console, no Python needed) is produced by build\build.ps1.
python scripts\gui_main.py
if errorlevel 1 pause
