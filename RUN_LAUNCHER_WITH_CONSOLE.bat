@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" chess_irl_launcher.py
pause
