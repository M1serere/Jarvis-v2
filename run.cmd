@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Сначала запусти setup.cmd.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
