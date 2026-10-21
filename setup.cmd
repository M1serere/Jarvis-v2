@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
py -3.13 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    py -3.13 -m venv .venv
    goto environment_created
)
py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    py -3 -m venv .venv
    goto environment_created
)
python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    python -m venv .venv
    goto environment_created
)
echo Установи Python 3.13, включив pip, Tcl/Tk и Add python.exe to PATH.
goto failed
:environment_created
if not exist ".venv\Scripts\python.exe" goto failed
:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" tools\download_model.py
if errorlevel 1 goto failed
echo.
echo Установка завершена. Для запуска открой run.cmd.
echo Город и микрофон можно выбрать в меню «Настройки».
pause
exit /b 0
:failed
echo.
echo Установка не завершена. Скопируй сообщение об ошибке выше.
pause
exit /b 1
