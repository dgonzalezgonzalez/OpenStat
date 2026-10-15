@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "OpenStat" ".venv\Scripts\pythonw.exe" -m openstat
) else (
    echo Create .venv and install ".[desktop]" first. See README.md.
    pause
)
