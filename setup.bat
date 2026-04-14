@echo off
echo ========================================
echo  AC Coach - One-Time Setup
echo ========================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Python found. Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ========================================
echo  Setup complete!
echo.
echo  NEXT STEP: Add your Anthropic API key.
echo  Open config.py and replace YOUR_API_KEY_HERE
echo  with your actual key from console.anthropic.com
echo ========================================
echo.
pause
