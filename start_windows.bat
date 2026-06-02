@echo off
setlocal
cd /d "%~dp0"

echo =====================================
echo RezDrop v0.4.16 - Windows start
echo =====================================
echo.

set "PY_CMD="

where py >nul 2>nul
if not errorlevel 1 (
  set "PY_CMD=py -3"
)

if "%PY_CMD%"=="" (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=python"
  )
)

if "%PY_CMD%"=="" (
  echo ERROR: Python was not found.
  echo Install Python 3.11+ and tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  echo Creating local config...
  copy ".env.local.example" ".env" >nul
  if errorlevel 1 goto fail
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto fail
)

echo Upgrading pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail

echo Installing requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo RezDrop is starting:
echo http://127.0.0.1:8080
echo.
echo Admin page:
echo http://127.0.0.1:8080/admin
echo.
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload

echo.
echo RezDrop stopped.
pause
exit /b 0

:fail
echo.
echo ERROR: launch failed.
echo Check the message above.
echo.
pause
exit /b 1
