@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" BACKUP_RAILWAY_DB_TO_LOCAL.py
) else (
  py -3.11 BACKUP_RAILWAY_DB_TO_LOCAL.py
)
pause
