@echo off
REM Batch wrapper to run the PowerShell Task Scheduler setup script
REM This will create a scheduled task that runs the GPU monitor with admin privileges

echo Setting up Task Scheduler for Enhanced GPU Monitor...
echo.
echo This will create a scheduled task that runs the GPU monitor with
echo administrator privileges on system startup, enabling power limit control.
echo.
echo NOTE: This script must be run as Administrator!
echo.

REM Check for administrator privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Running with administrator privileges - proceeding...
    echo.
    goto :run_setup
) else (
    echo ERROR: This script must be run as Administrator!
    echo.
    echo Right-click this file and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

:run_setup
cd /d "%~dp0"

REM Run the PowerShell script
powershell.exe -ExecutionPolicy Bypass -File "setup_gpu_monitor_task_scheduler.ps1"

pause

