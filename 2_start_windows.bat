@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0backend"
if not exist venv\Scripts\python.exe (
  echo [ERROR] Run 1_setup_windows.bat first.
  pause
  exit /b 1
)
venv\Scripts\python run_local.py
pause
