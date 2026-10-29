@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0backend"
venv\Scripts\python -W ignore -m app.jobs.daily_scan
pause
