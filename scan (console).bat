@echo off
cd /d "%~dp0"
REM Console fallback: the same scan the window runs, printed. Pass a folder to
REM scan it, or nothing for the saved / default folder. Results land in output\.
python scripts\cli.py %*
pause
