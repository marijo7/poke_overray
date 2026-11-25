@echo off
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python tools\update_usage.py %*
pause
