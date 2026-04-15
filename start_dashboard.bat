@echo off
cd /d "%~dp0"
echo Starting AC Coach Dashboard...
echo Your browser will open automatically.
echo Press Ctrl+C to stop.
echo.
"C:\Users\miave\AppData\Local\Programs\Python\Python313\Scripts\streamlit.exe" run dashboard.py
pause
