@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" RESTORE_LOCAL_DB_TO_RAILWAY.py
) else (
  py -3.11 RESTORE_LOCAL_DB_TO_RAILWAY.py
)
pause
