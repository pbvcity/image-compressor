@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 exit /b 2

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 (
        ".venv\Scripts\python.exe" "scripts\run_default.py"
        goto finish
    )
)
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    py -3 "scripts\run_default.py"
    goto finish
)
python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python "scripts\run_default.py"
    goto finish
)
python3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python3 "scripts\run_default.py"
    goto finish
)
echo Python 3.10+ is required. Install Python with "Add python.exe to PATH", then try again.
cmd /c exit 2

:finish
set "result=%errorlevel%"
popd
echo.
pause
exit /b %result%
