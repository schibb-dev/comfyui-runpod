@echo off
REM Enhanced GPU Monitor Startup Script

cd /d "%~dp0"

REM Use embedded Python if available, otherwise use system Python
if exist "python_embeded\python.exe" (
    set PYTHON_CMD=python_embeded\python.exe
) else (
    set PYTHON_CMD=python
)

echo Starting Enhanced GPU Monitor...
echo Logs will be written to C:\Logs\gpu_crash.log
echo.

%PYTHON_CMD% enhanced_gpu_monitor.py

timeout /t 10 /nobreak >nul

