@echo off
REM Double-click to launch PrintScout on Windows.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    py -3 -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r requirements-ml.txt
.venv\Scripts\python.exe -m printscout serve
pause
