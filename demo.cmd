@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запусти setup.cmd или выполни python main.py --demo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py --demo
if errorlevel 1 pause
