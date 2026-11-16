@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
title GitHub Star Daily

rem Keep this file pure ASCII. cmd decodes the batch file itself with the
rem active console code page, and UTF-8 multibyte characters get split
rem mid-sequence, which breaks even rem lines. Chinese output comes from Python.

rem ---- 1. Pick a Python >= 3.10 ----
rem Prefer the py launcher: it resolves an explicit version.
rem Relying on whichever "python" comes first in PATH is unreliable --
rem machines often have 3.8 / 3.10 / 3.12 installed side by side.
set "PY="
py -3.13 -c "import sys" >nul 2>nul && set "PY=py -3.13"
if not defined PY py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11"
if not defined PY py -3.10 -c "import sys" >nul 2>nul && set "PY=py -3.10"

rem No launcher available: fall back to python on PATH, if it is new enough.
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)

if not defined PY goto :no_python
echo   Python: %PY%

rem ---- 2. Install dependencies on first run ----
%PY% -c "import star_daily" >nul 2>nul
if errorlevel 1 (
  echo   First run detected. Installing dependencies, this may take a minute...
  echo.
  %PY% -m pip install -e . --progress-bar off --disable-pip-version-check
  if errorlevel 1 goto :install_failed
  echo.
)

rem ---- 3. Generate the report ----
if not exist ".env" (
  echo   Note: .env not found, running unauthenticated.
  echo         This round takes about 4-5 minutes.
  echo         To speed up: copy .env.example to .env and add a token.
  echo.
)

echo ============================================
echo   Generating daily report...
echo ============================================
echo.
%PY% -m star_daily run
set "RC=%ERRORLEVEL%"
echo.

if not "%RC%"=="0" goto :run_failed

echo ============================================
echo   Done. Report saved under: %~dp0reports
echo ============================================
if exist "reports" start "" "reports"
pause
exit /b 0

:no_python
echo.
echo   No suitable Python found. Version 3.10 or newer is required.
echo.
set "CURVER="
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set "CURVER=%%v"
if defined CURVER echo   Current "python" on PATH: %CURVER%
if not defined CURVER echo   Current "python" on PATH: not found
echo.
echo   Install from https://www.python.org/downloads/
echo   During setup, tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:install_failed
echo.
echo   Failed to install dependencies.
echo   Check your network or proxy, then run this file again.
echo.
pause
exit /b 1

:run_failed
echo ============================================
echo   Generation failed (exit code %RC%).
echo   See the messages above for details.
echo ============================================
pause
exit /b %RC%
