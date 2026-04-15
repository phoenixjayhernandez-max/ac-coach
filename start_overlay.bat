@echo off
cd /d "%~dp0"
echo Starting AC Coach Overlay...
echo Drag the window to position it. Press Q to close.
echo If a window does not appear, check overlay_error.txt for details.
echo.
"C:\Users\miave\AppData\Local\Programs\Python\Python313\python.exe" overlay.py 2> overlay_error.txt
if %errorlevel% neq 0 (
    echo.
    echo ERROR - see overlay_error.txt for details:
    type overlay_error.txt
)
pause
