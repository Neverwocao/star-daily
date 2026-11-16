@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
title GitHub Star Daily

rem ---- 1. 找一个能用的 Python ----
where python >nul 2>nul
if errorlevel 1 goto :no_python

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto :old_python

rem ---- 2. 首次运行自动装依赖 ----
python -c "import star_daily" >nul 2>nul
if errorlevel 1 (
  echo First run detected. Installing dependencies, this may take a minute...
  echo.
  python -m pip install -e . --quiet --disable-pip-version-check
  if errorlevel 1 goto :install_failed
  echo.
)

rem ---- 3. 生成日报 ----
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
python -m star_daily run
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
echo   Python not found.
echo   Install Python 3.11+ from https://www.python.org/downloads/
echo   During setup, tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:old_python
echo.
echo   Python 3.11 or newer is required. Found:
python --version
echo.
echo   Please upgrade from https://www.python.org/downloads/
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
