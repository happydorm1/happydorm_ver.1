@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0backend"
echo ==================================================
echo  Dormitory Loan System - first-time setup
echo ==================================================
set "PY="
py -3.13 -c "1" >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3.12 -c "1" >nul 2>&1 && set "PY=py -3.12"
if not defined PY python -c "import sys;sys.exit(0 if sys.version_info[:2] in ((3,12),(3,13)) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo [ERROR] Python 3.12 or 3.13 is required. https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Using: %PY%
if not exist venv\Scripts\python.exe %PY% -m venv venv
venv\Scripts\python -m pip install --upgrade pip -q
venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] package install failed
  pause
  exit /b 1
)
venv\Scripts\python local_setup.py
echo.
echo Done. Run 2_start_windows.bat to start.
pause
