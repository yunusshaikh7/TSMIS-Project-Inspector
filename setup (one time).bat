@echo off
cd /d "%~dp0"
echo Installing Python packages (openpyxl, pywebview)...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Is Python 3.11 installed and on PATH?
    pause
    exit /b 1
)
echo.
echo Setup complete. Run "run app.bat" for the window or "scan (console).bat" for the console.
pause
