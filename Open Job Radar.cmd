@echo off
setlocal
if not exist "%~dp0.venv\Scripts\pythonw.exe" (
    echo Job Radar's Python environment was not found.
    echo Open README.md in this folder for setup instructions.
    pause
    exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -X utf8 "%~dp0job_radar_gui.py"
