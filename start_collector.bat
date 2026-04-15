@echo off
cd /d "%~dp0"
echo Starting AC Telemetry Collector...
echo Keep this window open while you drive.
echo Press Ctrl+C to stop.
echo.
"C:\Users\miave\AppData\Local\Programs\Python\Python313\python.exe" collector.py
pause
