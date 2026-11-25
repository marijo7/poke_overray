@echo off
cd /d "%~dp0"
echo [1/3] Creating Python virtual environment...
py -3 -m venv .venv || python -m venv .venv
echo [2/3] Installing packages...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
echo [3/3] Downloading usage data...
.venv\Scripts\python tools\update_usage.py
echo.
echo Setup finished. Run start.bat to launch the overlay.
pause
