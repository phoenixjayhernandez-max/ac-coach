@echo off
cd /d "%~dp0"
"C:\Users\miave\AppData\Local\Programs\Python\Python313\python.exe" -c "
import sys
sys.path.insert(0, '.')
try:
    from telemetry.reader import ACTelemetryReader
    reader = ACTelemetryReader()
    result = reader.connect()
    if result:
        gfx = reader.read_graphics()
        phy = reader.read_physics()
        sta = reader.read_static()
        out = 'CONNECTED\nCar: ' + sta.carModel + '\nTrack: ' + sta.track + '\nSpeed: ' + str(round(phy.speedKmh,1)) + ' kmh\nStatus: ' + str(gfx.status)
        reader.disconnect()
    else:
        out = 'FAILED - AC not running or not on track'
except Exception as e:
    out = 'ERROR: ' + str(e)
with open('diag_result.txt', 'w') as f:
    f.write(out)
print(out)
" > diag_result.txt 2>&1
echo Done. See diag_result.txt
pause
