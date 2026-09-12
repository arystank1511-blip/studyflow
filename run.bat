@echo off
setlocal
cd /d "%~dp0"
title StudyFlow

where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from python.org, then run this file again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :error
)

echo Checking project dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo StudyFlow is starting at http://127.0.0.1:5000
echo Press Ctrl+C in this window when you want to stop it.
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
goto :end

:error
echo.
echo Setup failed. Check your Internet connection and Python installation, then try again.
pause

:end
endlocal
