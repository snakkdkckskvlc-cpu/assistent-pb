@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0poppler\Library\bin;%PATH%"
set "PYTHONPATH=%~dp0apps\backend\src;%~dp0packages\rag\src;%~dp0apps\desktop\src;%~dp0apps\desktop"
if "%LLM_MODEL%"=="" set "LLM_MODEL=qwen2.5:7b-instruct"
start "" "%~dp0venv\Scripts\pythonw.exe" -m fire_safety_desktop.main
endlocal
