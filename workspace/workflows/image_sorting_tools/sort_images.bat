@echo off
REM Image Content Sorter - Windows Batch Script
REM This script provides an easy way to run the image sorter on Windows

echo ================================
echo Image Content Sorter
echo ================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.7+ from https://python.org
    pause
    exit /b 1
)

REM Get the directory where this script is located
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Check if requirements are installed
echo Checking dependencies...
python -c "import torch, clip, PIL, sklearn, matplotlib" >nul 2>&1
if errorlevel 1 (
    echo.
    echo Some dependencies are missing. Would you like to install them now?
    echo This will run: pip install -r requirements_sorter.txt
    set /p install_deps="Install dependencies? (y/n): "
    if /i "%install_deps%"=="y" (
        echo Installing dependencies...
        pip install -r requirements_sorter.txt
        if errorlevel 1 (
            echo.
            echo ERROR: Failed to install dependencies
            echo Please install manually: pip install -r requirements_sorter.txt
            pause
            exit /b 1
        )
        echo Dependencies installed successfully!
        echo.
    ) else (
        echo Please install dependencies manually: pip install -r requirements_sorter.txt
        pause
        exit /b 1
    )
)

echo.
echo Choose sorting mode:
echo 1. Categories (sort by content type)
echo 2. Clustering (group similar images)
echo 3. Query (find images matching text)
echo 4. Advanced mode (custom command)
echo 5. Exit
echo.

set /p mode="Enter your choice (1-5): "

if "%mode%"=="1" goto categories
if "%mode%"=="2" goto clustering
if "%mode%"=="3" goto query
if "%mode%"=="4" goto advanced
if "%mode%"=="5" goto end
echo Invalid choice. Please enter 1-5.
pause
goto end

:categories
echo.
echo === CATEGORY-BASED SORTING ===
set /p input_dir="Enter input directory path: "
set /p output_dir="Enter output directory path: "
echo.
echo Choose action:
echo 1. Copy files (keeps originals)
echo 2. Move files (removes originals)
set /p action="Enter choice (1-2): "

if "%action%"=="2" (
    python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode categories --move
) else (
    python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode categories
)
goto success

:clustering
echo.
echo === CLUSTERING MODE ===
set /p input_dir="Enter input directory path: "
set /p output_dir="Enter output directory path: "
set /p clusters="Enter number of clusters (or press Enter for auto): "
echo.
echo Choose action:
echo 1. Copy files (keeps originals)
echo 2. Move files (removes originals)
set /p action="Enter choice (1-2): "

if "%clusters%"=="" (
    if "%action%"=="2" (
        python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode cluster --move
    ) else (
        python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode cluster
    )
) else (
    if "%action%"=="2" (
        python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode cluster --clusters %clusters% --move
    ) else (
        python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode cluster --clusters %clusters%
    )
)
goto success

:query
echo.
echo === QUERY MODE ===
set /p input_dir="Enter input directory path: "
set /p output_dir="Enter output directory path: "
set /p query_text="Enter search query (e.g., 'sunset landscape'): "
set /p top_k="Enter number of results to find (default 20): "
if "%top_k%"=="" set top_k=20

echo.
echo Choose action:
echo 1. Copy files (keeps originals)
echo 2. Move files (removes originals)
set /p action="Enter choice (1-2): "

if "%action%"=="2" (
    python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode query --query "%query_text%" --top-k %top_k% --move
) else (
    python advanced_image_sorter.py "%input_dir%" "%output_dir%" --mode query --query "%query_text%" --top-k %top_k%
)
goto success

:advanced
echo.
echo === ADVANCED MODE ===
echo Enter custom command (without 'python advanced_image_sorter.py'):
echo Example: "C:\Images" "C:\Sorted" --mode categories --config custom_config.yaml
set /p custom_cmd="Command: "
python advanced_image_sorter.py %custom_cmd%
goto success

:success
echo.
echo ================================
echo Operation completed successfully!
echo ================================
echo.
echo Check the output directory for results.
echo Press any key to continue...
pause >nul
goto end

:end
echo.
echo Goodbye!
pause
