@echo off
chcp 65001 >nul
cd /d "%~dp0"

python -c "import pynput, requests, PIL, PySide6" 2>nul
if errorlevel 1 (
    echo Устанавливаю зависимости...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Не удалось установить зависимости.
        pause
        exit /b 1
    )
)

python qt_app.py
if errorlevel 1 pause
