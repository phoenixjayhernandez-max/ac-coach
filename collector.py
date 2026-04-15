"""
Telemetry Collector — run this WHILE you are driving in Assetto Corsa.

It connects to AC's shared memory, polls at 10Hz, detects lap completions,
saves everything to the database, announces lap times via voice, and fires
a short AI coaching tip after each lap.

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
        self.session_id:       Optional[int]  = None
        self.last_lap_count:   int             = -1
        self.current_lap_id:   Optional[int]  = None
        self.tele_buffer:      list            = []   # accumulates 10Hz samples mid-lap
        self.lap_start_ms:     int             = 0
        self.max_speed:        float           = 0.0
        self.throttle_samples: list            = []
        self.brake_samples:    list            = []
        self.last_static_car:  str             = ""
        self.best_lap_ms:      int             = 0    # session personal best (for delta)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run():
    storage.init_db()
    reader = ACTelemetryReader()
    state  = SessionState()
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

            # -----------------------------------------------------------
            # Detect new session (car or track changed)
            # -----------------------------------------------------------
            car   = sta.carModel
            track = sta.track

            if car != state.last_static_car:
                player = f"{sta.playerName} {sta.playerSurname}".strip() or "Driver"
                state.session_id    = storage.create_session(track, car, player)
                state.last_static_car = car
                state.last_lap_count  = gfx.completedLaps
                state.best_lap_ms     = 0
                print(f"[collector] New session — {car} @ {track}  (ID {state.session_id})")

            if state.session_id is None:
                time.sleep(config.POLL_INTERVAL)
                continue

            # -----------------------------------------------------------
            # Real-time voice warnings (brake/tyre temps)
            # -----------------------------------------------------------
            if config.VOICE_ENABLED:
                try:
                    from coaching.voice_coach import check_warnings
                    check_warnings(phy)
                except Exception:
                    pass

            # -----------------------------------------------------------
            # Accumulate telemetry sample for this tick
            # -----------------------------------------------------------
            sample = (
                gfx.iCurrentTime,                    # timestamp_ms
                round(phy.speedKmh,         2),      # speed_kmh
                round(phy.gas,              4),      # throttle
                round(phy.brake,            4),      # brake
                phy.gear,                            # gear
                phy.rpm,                             # rpm
                round(phy.steerAngle,       4),      # steer_angle
                round(phy.tyreTempI[0],     1),      # tyre_temp_fl
                round(phy.tyreTempI[1],     1),      # tyre_temp_fr
                round(phy.tyreTempI[2],     1),      # tyre_temp_rl
                round(phy.tyreTempI[3],     1),      # tyre_temp_rr
                round(phy.tyreWear[0],      4),      # tyre_wear_fl
                round(phy.tyreWear[1],      4),      # tyre_wear_fr
                round(phy.tyreWear[2],      4),      # tyre_wear_rl
                round(phy.tyreWear[3],      4),      # tyre_wear_rr
                round(phy.brakeTemp[0],     1),      # brake_temp_fl
                round(phy.brakeTemp[1],     1),      # brake_temp_fr
                round(phy.brakeTemp[2],     1),      # brake_temp_rl
                round(phy.brakeTemp[3],     1),      # brake_temp_rr
                round(phy.suspensionTravel[0], 4),   # suspension_fl
                round(phy.suspensionTravel[1], 4),   # suspension_fr
                round(phy.suspensionTravel[2], 4),   # suspension_rl
                round(phy.suspensionTravel[3], 4),   # suspension_rr
                round(phy.accG[0],          4),      # g_lat  (lateral)
                round(phy.accG[2],          4),      # g_lon  (longitudinal)
                round(gfx.carCoordinates[0], 2),     # car_x
                round(gfx.carCoordinates[1], 2),     # car_y
                round(gfx.carCoordinates[2], 2),     # car_z
                round(gfx.normalizedCarPosition, 4), # normalized_pos  ← new
            )
            state.tele_buffer.append(sample)
            state.max_speed = max(state.max_speed, phy.speedKmh)
            state.throttle_samples.append(phy.gas)
            state.brake_samples.append(phy.brake)

            # -----------------------------------------------------------
            # Detect lap completion
            # -----------------------------------------------------------
            if gfx.completedLaps != state.last_lap_count and state.last_lap_count >= 0:
                lap_num = gfx.completedLaps
                lap_ms  = gfx.iLastTime

                if lap_ms > 0:
                    avg_thr = (
                        sum(state.throttle_samples) / len(state.throttle_samples)
                        if state.throttle_samples else 0
                    )
                    avg_brk = (
                        sum(state.brake_samples) / len(state.brake_samples)
                        if state.brake_samples else 0
                    )

                    lap_data = {
                        "lap_number":     lap_num,
                        "lap_time_ms":    lap_ms,
                        "sector1_ms":     0,
                        "sector2_ms":     0,
                        "sector3_ms":     0,
                        "is_valid":       1,
                        "tyre_compound":  gfx.tyreCompound,
                        "air_temp":       round(phy.airTemp,        1),
                        "road_temp":      round(phy.roadTemp,       1),
                        "fuel_remaining": round(phy.fuel,           2),
                        "max_speed_kmh":  round(state.max_speed,    1),
                        "avg_throttle":   round(avg_thr,            4),
                        "avg_brake":      round(avg_brk,            4),
                        "completed_at":   time.time(),
                    }
                    lap_id = storage.save_lap(state.session_id, lap_data)
                    storage.save_telemetry_batch(lap_id, state.tele_buffer)

                    # Delta vs session best
                    is_best = state.best_lap_ms == 0 or lap_ms < state.best_lap_ms
                    delta_ms = (lap_ms - state.best_lap_ms) if state.best_lap_ms > 0 else None
                    if is_best:
                        state.best_lap_ms = lap_ms

                    delta_str = ""
                    if delta_ms is not None:
                        sign = "+" if delta_ms >= 0 else ""
                        delta_str = f"  ({sign}{delta_ms/1000:.3f}s)"

                    pb_str = "  ★ PB!" if is_best else ""
                    print(
                        f"[collector] Lap {lap_num} — {ms_to_laptime(lap_ms)}"
                        f"{delta_str}{pb_str}  (max {state.max_speed:.0f} km/h)"
                    )

                    # Voice: announce lap time, then queue coaching tip
                    if config.VOICE_ENABLED:
                        try:
                            from coaching.voice_coach import announce_lap, post_lap_coaching_async
                            announce_lap(lap_ms, delta_ms=delta_ms, is_best=is_best)
                            post_lap_coaching_async(lap_id, state.session_id)
                        except Exception as e:
                            print(f"[collector] Voice error: {e}")

                # Reset for next lap
                state.tele_buffer      = []
                state.max_speed        = 0.0
                state.throttle_samples = []
                state.brake_samples    = []

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
