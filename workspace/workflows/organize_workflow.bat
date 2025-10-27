@echo off
REM Quick workflow organizer for Windows
REM Usage: organize_workflow.bat <workflow_file> <category>

if "%~2"=="" (
    echo Usage: organize_workflow.bat ^<workflow_file^> ^<category^>
    echo Categories: video-generation, character-generation, experimental, flux-generation, misc
    pause
    exit /b 1
)

set "workflow_file=%~1"
set "category=%~2"
set "timestamp=%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "timestamp=%timestamp: =0%"

if not exist "current\%category%" mkdir "current\%category%"

for %%f in ("%workflow_file%") do (
    set "filename=%%~nf"
    set "extension=%%~xf"
)

set "new_name=%filename%_%timestamp%%extension%"
move "%workflow_file%" "current\%category%\%new_name%"

echo ✅ Moved %workflow_file% to current\%category%\%new_name%
pause



