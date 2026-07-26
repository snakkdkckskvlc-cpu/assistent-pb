@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" -m fire_safety_desktop.main
    goto :end
)

echo Assistant PB is not installed yet. Running the installer (bootstrap.ps1)...
echo A Windows admin-rights prompt (UAC) will appear shortly - accept it to continue.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"

:end
endlocal
