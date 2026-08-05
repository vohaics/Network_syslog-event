@echo off
REM Windows launcher for Cisco Multi-Device Monitor
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py"
) else (
    set "PY=python"
)

%PY% -c "import flask" >nul 2>nul
if errorlevel 1 (
    echo Installing Flask...
    %PY% -m pip install flask
    if errorlevel 1 (
        echo.
        echo Failed to install Flask. Check your Python/pip installation.
        pause
        exit /b 1
    )
)

echo Starting dashboard on http://127.0.0.1:5000
echo Press Ctrl+C to stop.
echo.
%PY% cisco_multi_monitor.py

pause
