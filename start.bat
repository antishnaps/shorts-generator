@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1
if exist ".venv\Scripts\python.exe" goto launch
py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.12 x64 from python.org, then run this file again.
    pause
    exit /b 1
)
py -3.12 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:launch
".venv\Scripts\python.exe" main.py --smoke-test
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
    ".venv\Scripts\python.exe" main.py --smoke-test
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Setup or launch failed. No files have been deleted. Review the error above.
pause
exit /b 1
