@echo off
setlocal
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe chessmeet_remote_admin.py
) else (
  python chessmeet_remote_admin.py
)
