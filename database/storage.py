"""
All database operations. Uses SQLite which comes built into Python — no install needed.

Tables:
  sessions      — one row per AC session (track, car, date)
  laps          — one row per completed lap (times, conditions, aids)
  telemetry     — 10Hz samples during each lap (speed, throttle, brake, etc.)
"""

import sqlite3
import time
from typing import List, Optional, Dict, Any
import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row   # lets us access columns by name
    return conn


def init_db():
    """Create tables if they don't already exist."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                track       TEXT,
                car         TEXT,
                player_name TEXT,
                started_at  REAL    -- unix timestamp
            );

            CREATE TABLE IF NOT EXISTS laps (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER REFERENCES sessions(id),
                lap_number      INTEGER,
                lap_time_ms     INTEGER,   -- total lap time in milliseconds
                sector1_ms      INTEGER,
                sector2_ms      INTEGER,
                sector3_ms      INTEGER,
                is_valid        INTEGER DEFAULT 1,
                tyre_compound   TEXT,
                air_temp        REAL,
                road_temp       REAL,
                fuel_remaining  REAL,
                max_speed_kmh   REAL,
                avg_throttle    REAL,   -- 0.0 – 1.0
                avg_brake       REAL,   -- 0.0 – 1.0
                completed_at    REAL    -- unix timestamp
            );

            CREATE TABLE IF NOT EXISTS telemetry (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lap_id          INTEGER REFERENCES laps(id),
                timestamp_ms    INTEGER,   -- time within the lap
                speed_kmh       REAL,
                throttle        REAL,
                brake           REAL,
                gear            INTEGER,
                rpm             INTEGER,
                steer_angle     REAL,
                tyre_temp_fl    REAL,
                tyre_temp_fr    REAL,
                tyre_temp_rl    REAL,
                tyre_temp_rr    REAL,
                tyre_wear_fl    REAL,
                tyre_wear_fr    REAL,
                tyre_wear_rl    REAL,
                tyre_wear_rr    REAL,
                brake_temp_fl   REAL,
                brake_temp_fr   REAL,
                brake_temp_rl   REAL,
                brake_temp_rr   REAL,
                suspension_fl   REAL,
                suspension_fr   REAL,
                suspension_rl   REAL,
                suspension_rr   REAL,
                g_lat           REAL,
                g_lon           REAL,
                car_x           REAL,
                car_y           REAL,
                car_z           REAL
            );
        """)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(track: str, car: str, player_name: str) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (track, car, player_name, started_at) VALUES (?,?,?,?)",
            (track, car, player_name, time.time())
        )
        return cur.lastrowid


def get_session(session_id: int) -> Optional[Dict]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Laps
# ---------------------------------------------------------------------------

def save_lap(session_id: int, lap_data: dict) -> int:
    cols = ", ".join(lap_data.keys())
    placeholders = ", ".join(["?"] * len(lap_data))
    values = list(lap_data.values())
    with get_connection() as conn:
        cur = conn.execute(
            f"INSERT INTO laps (session_id, {cols}) VALUES (?, {placeholders})",
            [session_id] + values
        )
        return cur.lastrowid


def get_laps(session_id: Optional[int] = None) -> List[Dict]:
    with get_connection() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT l.*, s.track, s.car FROM laps l JOIN sessions s ON l.session_id=s.id "
                "WHERE l.session_id=? ORDER BY l.lap_number",
                (session_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT l.*, s.track, s.car FROM laps l JOIN sessions s ON l.session_id=s.id "
                "ORDER BY l.completed_at DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]


def get_best_lap(session_id: Optional[int] = None) -> Optional[Dict]:
    with get_connection() as conn:
        if session_id:
            row = conn.execute(
                "SELECT * FROM laps WHERE session_id=? AND is_valid=1 ORDER BY lap_time_ms ASC LIMIT 1",
                (session_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM laps WHERE is_valid=1 ORDER BY lap_time_ms ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None


def get_all_sessions() -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT s.*, COUNT(l.id) as lap_count, MIN(l.lap_time_ms) as best_lap_ms "
            "FROM sessions s LEFT JOIN laps l ON s.id=l.session_id "
            "GROUP BY s.id ORDER BY s.started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Telemetry samples
# ---------------------------------------------------------------------------

def save_telemetry_batch(lap_id: int, samples: list):
    """Insert many telemetry rows at once for performance."""
    if not samples:
        return
    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO telemetry (
                lap_id, timestamp_ms, speed_kmh, throttle, brake, gear, rpm,
                steer_angle, tyre_temp_fl, tyre_temp_fr, tyre_temp_rl, tyre_temp_rr,
                tyre_wear_fl, tyre_wear_fr, tyre_wear_rl, tyre_wear_rr,
                brake_temp_fl, brake_temp_fr, brake_temp_rl, brake_temp_rr,
                suspension_fl, suspension_fr, suspension_rl, suspension_rr,
                g_lat, g_lon, car_x, car_y, car_z
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(lap_id,) + tuple(s) for s in samples]
        )


def get_telemetry(lap_id: int) -> List[Dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM telemetry WHERE lap_id=? ORDER BY timestamp_ms",
            (lap_id,)
        ).fetchall()
        return [dict(r) for r in rows]
