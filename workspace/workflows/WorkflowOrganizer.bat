@echo off
title ComfyUI Workflow Organizer
cd /d "%~dp0"

:menu
cls
echo ========================================
echo    ComfyUI Workflow Organizer
echo ========================================
echo.
echo Available workflows in root directory:
echo.
dir *.json /b 2>nul | findstr /v "current legacy"
echo.
echo Categories:
echo 1. video-generation
echo 2. character-generation  
echo 3. experimental
echo 4. flux-generation
echo 5. misc
echo.
echo Note: FaceBlast workflows should go in video-generation
echo.
set /p choice="Enter workflow filename (or 'q' to quit): "

if "%choice%"=="q" exit

if not exist "%choice%" (
    echo File not found: %choice%
    pause
    goto menu
)

echo.
echo Select category:
echo 1. video-generation
echo 2. character-generation
echo 3. experimental
echo 4. flux-generation
echo 5. misc
set /p cat="Enter category number (1-5): "

if "%cat%"=="1" set "category=video-generation"
if "%cat%"=="2" set "category=character-generation"
if "%cat%"=="3" set "category=experimental"
if "%cat%"=="4" set "category=flux-generation"
if "%cat%"=="5" set "category=misc"

if not defined category (
    echo Invalid category selection
    pause
    goto menu
)

echo.
echo Moving %choice% to current\%category%\...
call organize_workflow.bat "%choice%" "%category%"
echo.
pause
goto menu
