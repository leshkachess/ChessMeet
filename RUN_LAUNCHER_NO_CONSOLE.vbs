Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run Chr(34) & ".venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & "chess_irl_launcher.py" & Chr(34), 0, False
