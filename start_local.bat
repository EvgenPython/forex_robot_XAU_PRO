@echo off
cd /d "%~dp0"

set "PYTHON_EXE=python"
if exist "%~dp0venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0venv\Scripts\python.exe"

start "MT5 Trading Robot" cmd /k ""%PYTHON_EXE%" "%~dp0run_bot.py""
start "MT5 Robot Watchdog" cmd /k ""%PYTHON_EXE%" "%~dp0run_watchdog.py""
