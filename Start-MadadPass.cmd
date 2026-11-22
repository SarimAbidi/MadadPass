@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.11 or newer and make sure python is on PATH.
    pause
    exit /b 1
)
python -c "import starlette, uvicorn, jsonschema" >nul 2>&1
if errorlevel 1 (
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
    ".venv\Scripts\python.exe" -c "import starlette, uvicorn, jsonschema" >nul 2>&1
    if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    ".venv\Scripts\python.exe" run.py %*
) else (
    python run.py %*
)
if errorlevel 1 pause
