@echo off
REM Enhanced GPU Monitor Startup Script (Elevated/Administrator)
REM This script requests administrator privileges to enable power limit control

REM Check for administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrator privileges - power limit control enabled
    goto :run_monitor
) else (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:run_monitor
cd /d "%~dp0"

REM Use embedded Python if available, otherwise use system Python
if exist "python_embeded\python.exe" (
    set PYTHON_CMD=python_embeded\python.exe
) else (
    set PYTHON_CMD=python
)

echo Starting Enhanced GPU Monitor with administrator privileges...
echo Power limit control: ENABLED
echo Logs will be written to C:\Logs\gpu_crash.log
echo.

%PYTHON_CMD% enhanced_gpu_monitor.py

timeout /t 10 /nobreak >nul

