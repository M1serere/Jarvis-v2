@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Сначала запусти setup.cmd.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" tools\setup_model.py
if errorlevel 1 (
    echo Установка модели не завершена. Готовые команды работают без неё.
    pause
    exit /b 1
)
pause
