@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Primero ejecuta instalar.bat
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" traductor.py
