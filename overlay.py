"""
AC Coach — Live On-Screen Overlay.

Shows speed, gear, current lap time, delta to personal best,
and tyre temperatures in an always-on-top window.

Run alongside collector.py:
    python overlay.py

Drag the window with left-click. Press Q to quit.
"""

import tkinter as tk
import threading
import time
from typing import Optional, Dict

import config
from telemetry.reader import ACTelemetryReader, AC_LIVE, ms_to_laptime
from database import storage


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def tyre_colour(temp: float) -> str:
    """Colour-code tyre temperature: cold=blue, ideal=green, warm=orange, hot=red."""
    if temp <= 0:
        return "#888888"
    elif temp < 70:
        return "#4488ff"
    elif temp < 90:
        return "#44cc44"
    elif temp < 105:
        return "#ffaa00"
    else:
        return "#ff4444"


# ---------------------------------------------------------------------------
# Reference lap — loads best lap's normalizedCarPosition → timestamp lookup
# ---------------------------------------------------------------------------

class ReferenceLap:
    def __init__(self):
        self._lookup: Dict[int, int] = {}
        self._best_ms: int = 0
        self._loaded_track: str = ""
        self._loaded_car: str = ""

    def load(self, track: str, car: str):
        if track == self._loaded_track and car == self._loaded_car:
            return   # already loaded for this combo
        try:
            with storage.get_connection() as conn:
                row = conn.execute(
                    """SELECT l.id, l.lap_time_ms
                       FROM laps l
                       JOIN sessions s ON l.session_id = s.id
                       WHERE s.track=? AND s.car=?
                         AND l.is_valid=1 AND l.lap_time_ms > 0
                       ORDER BY l.lap_time_ms ASC LIMIT 1""",
                    (track, car),
                ).fetchone()

            if not row:
                self._lookup = {}
                self._best_ms = 0
                return

            lap_id, best_ms = row[0], row[1]
            tele = storage.get_telemetry(lap_id)

            lookup: Dict[int, int] = {}
            for s in tele:
                pos = s.get("normalized_pos")
                if pos is not None and pos > 0:
                    bucket = int(pos * 1000)
                    if bucket not in lookup:
                        lookup[bucket] = s["timestamp_ms"]

            self._lookup    = lookup
            self._best_ms   = best_ms
            self._loaded_track = track
            self._loaded_car   = car

        except Exception:
            self._lookup = {}
            self._best_ms = 0

    def get_delta(self, normalized_pos: float, current_ms: int) -> Optional[int]:
        """Return delta in ms vs reference lap at the same track position."""
        if not self._lookup:
            return None
        bucket = int(normalized_pos * 1000)
        for offset in range(25):
            for b in (bucket + offset, bucket - offset):
                if b in self._lookup:
                    return current_ms - self._lookup[b]
        return None

    @property
    def best_ms(self) -> int:
        return self._best_ms


# ---------------------------------------------------------------------------
# Overlay window
# ---------------------------------------------------------------------------

class ACOverlay:
    BG     = "#141414"
    FG     = "#ffffff"
    DIM    = "#777777"
    YELLOW = "#ffcc00"
    GREEN  = "#44cc44"
    RED    = "#ff4444"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AC Coach")
        self.root.configure(bg=self.BG)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.88)
        self.root.overrideredirect(True)     # no title bar
        self.root.geometry("270x240+20+20")

        self._build_ui()
        self._make_draggable()

        self.reader  = ACTelemetryReader()
        self.ref_lap = ReferenceLap()

        self._running       = True
        self._current_track = ""
        self._current_car   = ""
        self._prev_laps     = -1

        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

        self.root.bind("<KeyPress-q>", lambda _: self.quit())
        self.root.bind("<KeyPress-Q>", lambda _: self.quit())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        pad = dict(padx=10, pady=2)

        # Speed — largest element
        self.speed_var = tk.StringVar(value="--- km/h")
        tk.Label(
            self.root, textvariable=self.speed_var,
            font=("Consolas", 30, "bold"), bg=self.BG, fg=self.FG,
        ).pack(**pad)

        # Gear + RPM
        row1 = tk.Frame(self.root, bg=self.BG)
        row1.pack(fill="x", padx=10, pady=0)
        self.gear_var = tk.StringVar(value="N")
        tk.Label(
            row1, textvariable=self.gear_var,
            font=("Consolas", 20, "bold"), bg=self.BG, fg=self.YELLOW,
        ).pack(side="left")
        self.rpm_var = tk.StringVar(value="")
        tk.Label(
            row1, textvariable=self.rpm_var,
            font=("Consolas", 11), bg=self.BG, fg=self.DIM,
        ).pack(side="right")

        # Lap time + Delta
        row2 = tk.Frame(self.root, bg=self.BG)
        row2.pack(fill="x", padx=10, pady=2)
        self.laptime_var = tk.StringVar(value="-:--.---")
        tk.Label(
            row2, textvariable=self.laptime_var,
            font=("Consolas", 14), bg=self.BG, fg=self.FG,
        ).pack(side="left")
        self.delta_var = tk.StringVar(value="")
        self.delta_lbl = tk.Label(
            row2, textvariable=self.delta_var,
            font=("Consolas", 14, "bold"), bg=self.BG, fg=self.GREEN,
        )
        self.delta_lbl.pack(side="right")

        # Divider
        tk.Frame(self.root, bg="#2a2a2a", height=1).pack(fill="x", padx=10, pady=6)

        # Tyre temps
        tk.Label(
            self.root, text="TYRE TEMPS",
            font=("Consolas", 8), bg=self.BG, fg=self.DIM,
        ).pack()

        tyre_frame = tk.Frame(self.root, bg=self.BG)
        tyre_frame.pack(padx=10, pady=2)

        self.tyre_vars: Dict[str, tk.StringVar] = {}
        self.tyre_lbls: Dict[str, tk.Label]     = {}
        for pos, r, c in [("FL", 0, 0), ("FR", 0, 1), ("RL", 1, 0), ("RR", 1, 1)]:
            f = tk.Frame(tyre_frame, bg=self.BG)
            f.grid(row=r, column=c, padx=14, pady=3)
            tk.Label(f, text=pos, font=("Consolas", 8), bg=self.BG, fg=self.DIM).pack()
            v = tk.StringVar(value="--°")
            lbl = tk.Label(f, textvariable=v, font=("Consolas", 13, "bold"), bg=self.BG, fg=self.FG)
            lbl.pack()
            self.tyre_vars[pos] = v
            self.tyre_lbls[pos] = lbl

        # Status
        self.status_var = tk.StringVar(value="Waiting for Assetto Corsa…")
        tk.Label(
            self.root, textvariable=self.status_var,
            font=("Consolas", 7), bg=self.BG, fg=self.DIM,
        ).pack(pady=4)

    def _make_draggable(self):
        self.root.bind("<Button-1>",  self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)

    def _drag_start(self, e):
        self._ox, self._oy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._ox
        y = self.root.winfo_y() + e.y - self._oy
        self.root.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------
    # Data loop (runs in background thread)
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            if not self.reader.connected:
                if not self.reader.connect():
                    self._safe_set(self.status_var, "Waiting for Assetto Corsa…")
                    time.sleep(2)
                    continue

            try:
                gfx = self.reader.read_graphics()
                phy = self.reader.read_physics()
                sta = self.reader.read_static()

                if gfx is None or phy is None or sta is None:
                    time.sleep(0.1)
                    continue

                if gfx.status != AC_LIVE:
                    self._safe_set(self.status_var, "AC not live")
                    time.sleep(0.5)
                    continue

                track = sta.track
                car   = sta.carModel

                if track != self._current_track or car != self._current_car:
                    self._current_track = track
                    self._current_car   = car
                    self.ref_lap.load(track, car)
                    self._safe_set(self.status_var, f"{track}  |  {car}")

                # Reload reference if a lap completed (possible new best)
                if gfx.completedLaps != self._prev_laps:
                    self._prev_laps = gfx.completedLaps
                    self.ref_lap.load(track, car)

                norm_pos = gfx.normalizedCarPosition
                delta_ms = self.ref_lap.get_delta(norm_pos, gfx.iCurrentTime)

                self._refresh(gfx, phy, delta_ms)

            except Exception:
                self.reader.disconnect()
                time.sleep(1)

            time.sleep(0.1)

    def _refresh(self, gfx, phy, delta_ms: Optional[int]):
        self._safe_set(self.speed_var, f"{phy.speedKmh:.0f} km/h")

        # Gear: 0=R, 1=N, 2+=1st,2nd,...
        g = phy.gear
        gear_str = "R" if g == 0 else ("N" if g == 1 else str(g - 1))
        self._safe_set(self.gear_var, f"G{gear_str}")
        self._safe_set(self.rpm_var,  f"{phy.rpm:,} rpm")
        self._safe_set(self.laptime_var, ms_to_laptime(gfx.iCurrentTime))

        if delta_ms is not None:
            sign   = "+" if delta_ms >= 0 else "-"
            colour = self.RED if delta_ms >= 0 else self.GREEN
            self._safe_set(self.delta_var, f"{sign}{abs(delta_ms)/1000:.2f}")
            self.root.after(0, lambda c=colour: self.delta_lbl.configure(fg=c))
        else:
            self._safe_set(self.delta_var, "")

        temps = {
            "FL": phy.tyreTempI[0], "FR": phy.tyreTempI[1],
            "RL": phy.tyreTempI[2], "RR": phy.tyreTempI[3],
        }
        for pos, temp in temps.items():
            self._safe_set(self.tyre_vars[pos], f"{temp:.0f}°")
            col = tyre_colour(temp)
            lbl = self.tyre_lbls[pos]
            self.root.after(0, lambda l=lbl, c=col: l.configure(fg=c))

    def _safe_set(self, var: tk.StringVar, value: str):
        self.root.after(0, lambda: var.set(value))

    # ------------------------------------------------------------------

    def quit(self):
        self._running = False
        self.reader.disconnect()
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    storage.init_db()
    ACOverlay().run()
