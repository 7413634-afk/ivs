@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo Python не знайдено. Встановіть Python 3.10 або новіший з https://www.python.org
  pause
  exit /b
)
%PY% -c "import matplotlib, serial" 2>nul || %PY% -m pip install --user matplotlib pyserial
%PY% main.py
if errorlevel 1 pause
