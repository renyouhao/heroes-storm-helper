@echo off
setlocal
set "preview_python=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%preview_python%" (
    echo Project Python environment was not found.
    echo Run the project dependency installer first.
    pause
    exit /b 1
)
start "" "%preview_python%" "%~dp0tools\preview_overlay_designs.py" %*
