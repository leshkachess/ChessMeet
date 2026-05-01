@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Building ChessIRL_Server_Launcher.exe...
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Put this file in project root.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --onefile --windowed --name ChessIRL_Server_Launcher chess_irl_launcher.py
if exist "dist\ChessIRL_Server_Launcher.exe" (
  copy /Y "dist\ChessIRL_Server_Launcher.exe" ".\ChessIRL_Server_Launcher.exe"
  echo.
  echo DONE: ChessIRL_Server_Launcher.exe created.
) else (
  echo Build failed.
)
pause
