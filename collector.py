"""
Telemetry Collector — run this WHILE you are driving in Assetto Corsa.

It connects to AC's shared memory, polls at 10Hz, detects lap completions,
and saves everything to the database automatically.

Usage (Windows Command Prompt):
    python collector.py
"""

import time
import sys
import signal
from typing import Optional

import config
from database import storage
from telemetry.reader import ACTelemetryReader, ms_to_laptime, AC_LIVE


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

class SessionState:
    def __init__(self):
        self.session_id:   Optional[int] = None
        self.last_lap_count: int = -1
        self.current_lap_id: Optional[int] = None
        self.tele_buffer: list = []          # accumulates 10Hz samples mid-lap
        self.lap_start_ms: int = 0
        self.max_speed: float = 0.0
        self.throttle_samples: list = []
        self.brake_samples: list = []
        self.last_static_car: str = ""


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    storage.init_db()
    reader = ACTelemetryReader()
    state = SessionState()
    running = True

    def shutdown(sig, frame):
        nonlocal running
        print("\n[collector] Shutting down...")
        running = False

    signal.signal(signal.SIGINT, shutdown)

    print("[collector] Waiting for Assetto Corsa...")
    print("[collector] Press Ctrl+C to stop.\n")

    while running:
        # Try to connect (or reconnect) to AC
        if not reader.connected:
            if not reader.connect():
                time.sleep(2)
                continue
            print("[collector] Connected to Assetto Corsa.")

        try:
            gfx = reader.read_graphics()
            phy = reader.read_physics()
            sta = reader.read_static()

            if gfx is None or phy is None or sta is None:
                time.sleep(config.POLL_INTERVAL)
                continue

            # Only collect when the game is live (not paused/replay/menu)
            if gfx.status != AC_LIVE:
                time.sleep(0.5)
                continue

            # ---------------------------------------------------------------
            # Detect new session (car or track changed)
            # ---------------------------------------------------------------
            car   = sta.carModel
            track = sta.track

            if car != state.last_static_car:
                player = f"{sta.playerName} {sta.playerSurname}".strip() or "Driver"
                state.session_id = storage.create_session(track, car, player)
                state.last_static_car = car
                state.last_lap_count = gfx.completedLaps
                print(f"[collector] New session — {car} @ {track}  (ID {state.session_id})")

            if state.session_id is None:
                time.sleep(config.POLL_INTERVAL)
                continue

            # ---------------------------------------------------------------
            # Accumulate telemetry sample for this tick
            # ---------------------------------------------------------------
            current_time_ms = gfx.iCurrentTime

            sample = (
                current_time_ms,
                round(phy.speedKmh, 2),
                round(phy.gas, 4),
                round(phy.brake, 4),
                phy.gear,
                phy.rpm,
                round(phy.steerAngle, 4),
                round(phy.tyreTempI[0], 1),   # FL inner
                round(phy.tyreTempI[1], 1),   # FR inner
                round(phy.tyreTempI[2], 1),   # RL inner
                round(phy.tyreTempI[3], 1),   # RR inner
                round(phy.tyreWear[0], 4),
                round(phy.tyreWear[1], 4),
                round(phy.tyreWear[2], 4),
                round(phy.tyreWear[3], 4),
                round(phy.brakeTemp[0], 1),
                round(phy.brakeTemp[1], 1),
                round(phy.brakeTemp[2], 1),
                round(phy.brakeTemp[3], 1),
                round(phy.suspensionTravel[0], 4),
                round(phy.suspensionTravel[1], 4),
                round(phy.suspensionTravel[2], 4),
                round(phy.suspensionTravel[3], 4),
                round(phy.accG[0], 4),   # lateral G
                round(phy.accG[2], 4),   # longitudinal G
                round(gfx.carCoordinates[0], 2),
                round(gfx.carCoordinates[1], 2),
                round(gfx.carCoordinates[2], 2),
            )
            state.tele_buffer.append(sample)
            state.max_speed = max(state.max_speed, phy.speedKmh)
            state.throttle_samples.append(phy.gas)
            state.brake_samples.append(phy.brake)

            # ---------------------------------------------------------------
            # Detect lap completion
            # ---------------------------------------------------------------
            if gfx.completedLaps != state.last_lap_count and state.last_lap_count >= 0:
                lap_num = gfx.completedLaps
                lap_ms  = gfx.iLastTime

                if lap_ms > 0:
                    avg_thr = sum(state.throttle_samples) / len(state.throttle_samples) if state.throttle_samples else 0
                    avg_brk = sum(state.brake_samples)    / len(state.brake_samples)    if state.brake_samples    else 0

                    lap_data = {
                        "lap_number":     lap_num,
                        "lap_time_ms":    lap_ms,
                        "sector1_ms":     0,   # AC doesn't always expose sector splits via SHM
                        "sector2_ms":     0,
                        "sector3_ms":     0,
                        "is_valid":       1,
                        "tyre_compound":  gfx.tyreCompound,
                        "air_temp":       round(phy.airTemp, 1),
                        "road_temp":      round(phy.roadTemp, 1),
                        "fuel_remaining": round(phy.fuel, 2),
                        "max_speed_kmh":  round(state.max_speed, 1),
                        "avg_throttle":   round(avg_thr, 4),
                        "avg_brake":      round(avg_brk, 4),
                        "completed_at":   time.time(),
                    }
                    lap_id = storage.save_lap(state.session_id, lap_data)
                    storage.save_telemetry_batch(lap_id, state.tele_buffer)
                    print(f"[collector] Lap {lap_num} saved — {ms_to_laptime(lap_ms)}  "
                          f"(max {state.max_speed:.0f} km/h)")

                # Reset for next lap
                state.tele_buffer = []
                state.max_speed = 0.0
                state.throttle_samples = []
                state.brake_samples = []

            state.last_lap_count = gfx.completedLaps

        except Exception as e:
            print(f"[collector] Error: {e}")
            reader.disconnect()
            time.sleep(2)

        time.sleep(config.POLL_INTERVAL)

    reader.disconnect()
    print("[collector] Stopped.")


if __name__ == "__main__":
    run()
