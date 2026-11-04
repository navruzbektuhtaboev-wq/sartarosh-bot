@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
title Sartarosh bot

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python topilmadi. https://www.python.org/downloads/ dan o'rnating
  echo va o'rnatishda "Add python.exe to PATH" belgisini qo'ying.
  pause
  exit /b
)

if not exist .venv (
  echo Birinchi marta o'rnatilmoqda, 1-2 daqiqa kuting...
  python -m venv .venv
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt

:loop
echo Bot ishga tushmoqda...
.venv\Scripts\python -m bot.main
echo Bot to'xtadi, 10 soniyadan keyin qayta ishga tushadi...
timeout /t 10 >nul
goto loop
