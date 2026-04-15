"""
AC Coach — Live On-Screen Overlay.
Drag with left-click. Press Q to quit.
Click km/h or °C labels to toggle units.
"""

import tkinter as tk
import threading
import time
import ctypes
from typing import Optional, Dict

import config
from telemetry.reader import ACTelemetryReader, AC_LIVE, ms_to_laptime
from database import storage


# ---------------------------------------------------------------------------
# Fix DPI scaling (removes graininess on Windows)
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Win32 topmost
# ---------------------------------------------------------------------------
def _force_topmost(hwnd):
    try:
        ctypes.windll.user32.SetWindowPos(
            hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0010
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
BG      = "#111111"
BG2     = "#181818"
BG3     = "#1f1f1f"
FG      = "#f0f0f0"
DIM     = "#4a4a4a"
ACCENT  = "#ffd000"
GREEN   = "#00e676"
RED     = "#ff3d57"
ORANGE  = "#ff9100"
BLUE    = "#4da6ff"

FONT_NUM  = ("Consolas",  )   # monospace for numbers
FONT_UI   = ("Segoe UI",  )   # smooth for labels


def rpm_col(p):
    return GREEN if p < 0.65 else (ORANGE if p < 0.85 else RED)

def delta_col(ms):
    return GREEN if ms < 0 else RED

def tyre_col(t):
    if t <= 0:    return DIM
    elif t < 70:  return BLUE
    elif t < 85:  return GREEN
    elif t < 100: return ORANGE
    else:         return RED

def fuel_col(p):
    return GREEN if p > 0.3 else (ORANGE if p > 0.15 else RED)


# ---------------------------------------------------------------------------
# Reference lap
# ---------------------------------------------------------------------------
class ReferenceLap:
    def __init__(self):
        self._lk   = {}
        self._best = 0
        self._track = ""
        self._car   = ""

    def load(self, track, car):
        if track == self._track and car == self._car:
            return
        self._track = track
        self._car   = car
        self._lk    = {}
        self._best  = 0
        try:
            with storage.get_connection() as conn:
                row = conn.execute(
                    "SELECT l.id, l.lap_time_ms FROM laps l "
                    "JOIN sessions s ON l.session_id=s.id "
                    "WHERE s.track=? AND s.car=? AND l.is_valid=1 AND l.lap_time_ms>0 "
                    "ORDER BY l.lap_time_ms ASC LIMIT 1",
                    (track, car),
                ).fetchone()
            if not row:
                return
            tele = storage.get_telemetry(row[0])
            lk = {}
            for s in tele:
                p = s.get("normalized_pos")
                if p and p > 0:
                    b = int(p * 1000)
                    if b not in lk:
                        lk[b] = s["timestamp_ms"]
            self._lk   = lk
            self._best = row[1]
        except Exception:
            pass

    def delta(self, pos, cur_ms):
        if not self._lk:
            return None
        b = int(pos * 1000)
        for off in range(25):
            for x in (b + off, b - off):
                if x in self._lk:
                    return cur_ms - self._lk[x]
        return None

    @property
    def best_ms(self):
        return self._best


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------
class ACOverlay:
    W, H = 290, 390

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AC Coach")
        self.root.configure(bg=BG)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.95)
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.W}x{self.H}+20+20")

        # Unit preferences (toggled by clicking labels)
        self._use_mph = False
        self._use_f   = False

        self._build()
        self._draggable()
        self.root.after(600,  self._pin)
        self.root.after(2000, self._keep_pinned)

        self.reader = ACTelemetryReader()
        self.ref    = ReferenceLap()
        self._running   = True
        self._max_rpm   = 8000
        self._max_fuel  = 90.0
        self._track     = ""
        self._car       = ""
        self._prev_laps = -1
        self._state: dict = {}

        threading.Thread(target=self._read_loop, daemon=True).start()
        self.root.after(100, self._tick)
        self.root.bind("<KeyPress-q>", lambda _: self.quit())
        self.root.bind("<KeyPress-Q>", lambda _: self.quit())

    # ------------------------------------------------------------------ pin
    def _pin(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            _force_topmost(hwnd)
        except Exception:
            pass

    def _keep_pinned(self):
        if self._running:
            self._pin()
            self.root.wm_attributes("-topmost", True)
            self.root.after(2000, self._keep_pinned)

    # ------------------------------------------------------------------ UI
    def _sep(self, pady=4):
        tk.Frame(self.root, bg=BG3, height=1).pack(fill="x", padx=10, pady=pady)

    def _build(self):
        r = self.root
        pad = dict(padx=12)

        # ── Header ───────────────────────────────────────────────────────
        self.v_header = tk.StringVar(value="Waiting for Assetto Corsa…")
        tk.Label(r, textvariable=self.v_header,
                 font=(*FONT_UI, 8), bg=BG, fg=ACCENT,
                 anchor="center").pack(fill="x", **pad, pady=(8, 3))

        self._sep(pady=2)

        # ── Speed ────────────────────────────────────────────────────────
        spd_row = tk.Frame(r, bg=BG)
        spd_row.pack(fill="x", **pad, pady=(6, 0))

        self.v_speed = tk.StringVar(value="0")
        tk.Label(spd_row, textvariable=self.v_speed,
                 font=(*FONT_NUM, 44, "bold"), bg=BG, fg=FG,
                 anchor="e").pack(side="left")

        unit_col = tk.Frame(spd_row, bg=BG)
        unit_col.pack(side="left", padx=(4, 0), pady=(20, 0))

        self.v_speed_unit = tk.StringVar(value="km/h")
        spd_unit_lbl = tk.Label(unit_col, textvariable=self.v_speed_unit,
                 font=(*FONT_UI, 9), bg=BG, fg=DIM,
                 cursor="hand2", anchor="w")
        spd_unit_lbl.pack(anchor="w")
        spd_unit_lbl.bind("<Button-1>", self._toggle_speed)

        # ── Gear + RPM bar ───────────────────────────────────────────────
        grpm = tk.Frame(r, bg=BG)
        grpm.pack(fill="x", **pad, pady=(2, 0))

        self.v_gear = tk.StringVar(value="N")
        tk.Label(grpm, textvariable=self.v_gear,
                 font=(*FONT_NUM, 20, "bold"), bg=BG, fg=ACCENT,
                 width=3, anchor="w").pack(side="left")

        rhs = tk.Frame(grpm, bg=BG)
        rhs.pack(side="left", fill="x", expand=True)

        self.v_rpm = tk.StringVar(value="")
        tk.Label(rhs, textvariable=self.v_rpm,
                 font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 anchor="e").pack(fill="x")

        rpm_bg = tk.Frame(rhs, bg=BG2, height=8)
        rpm_bg.pack(fill="x", pady=(1, 0))
        rpm_bg.pack_propagate(False)
        self._c_rpm = tk.Canvas(rpm_bg, bg=BG2, height=8,
                                highlightthickness=0, bd=0)
        self._c_rpm.pack(fill="both", expand=True)

        self._sep()

        # ── Lap time + Delta ─────────────────────────────────────────────
        lt = tk.Frame(r, bg=BG)
        lt.pack(fill="x", **pad)

        self.v_lap = tk.StringVar(value="-:--.---")
        tk.Label(lt, textvariable=self.v_lap,
                 font=(*FONT_NUM, 17, "bold"), bg=BG, fg=FG,
                 anchor="w").pack(side="left")

        self.v_delta = tk.StringVar(value="")
        self.lbl_delta = tk.Label(lt, textvariable=self.v_delta,
                 font=(*FONT_NUM, 17, "bold"), bg=BG, fg=GREEN, anchor="e")
        self.lbl_delta.pack(side="right")

        # Delta bar
        dbar = tk.Frame(r, bg=BG2, height=4)
        dbar.pack(fill="x", padx=12, pady=(3, 0))
        dbar.pack_propagate(False)
        self._c_delta = tk.Canvas(dbar, bg=BG2, height=4,
                                  highlightthickness=0, bd=0)
        self._c_delta.pack(fill="both", expand=True)

        # Best + Lap # + Pos
        meta = tk.Frame(r, bg=BG)
        meta.pack(fill="x", **pad, pady=(4, 2))
        self.v_best = tk.StringVar(value="Best  --:--.---")
        tk.Label(meta, textvariable=self.v_best,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="w").pack(side="left")
        self.v_lapnum = tk.StringVar(value="")
        tk.Label(meta, textvariable=self.v_lapnum,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="e").pack(side="right")

        self._sep()

        # ── Tyre temps ───────────────────────────────────────────────────
        t_head = tk.Frame(r, bg=BG)
        t_head.pack(fill="x", **pad)
        tk.Label(t_head, text="TYRES",
                 font=(*FONT_UI, 7), bg=BG, fg=DIM, anchor="w").pack(side="left")
        self.v_temp_unit = tk.StringVar(value="°C")
        temp_unit_lbl = tk.Label(t_head, textvariable=self.v_temp_unit,
                 font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 cursor="hand2", anchor="e")
        temp_unit_lbl.pack(side="right")
        temp_unit_lbl.bind("<Button-1>", self._toggle_temp)

        tgrid = tk.Frame(r, bg=BG)
        tgrid.pack(pady=(3, 0))

        self._tv: Dict[str, tk.StringVar] = {}
        self._tl: Dict[str, tk.Label]     = {}

        for pos, row, col in [("FL",0,0),("FR",0,1),("RL",1,0),("RR",1,1)]:
            cell = tk.Frame(tgrid, bg=BG2, width=56, height=44)
            cell.grid(row=row, column=col, padx=4, pady=3)
            cell.grid_propagate(False)
            tk.Label(cell, text=pos, font=(*FONT_UI, 7),
                     bg=BG2, fg=DIM).place(relx=0.5, y=5, anchor="n")
            v = tk.StringVar(value="--°")
            lbl = tk.Label(cell, textvariable=v,
                           font=(*FONT_NUM, 13, "bold"),
                           bg=BG2, fg=FG)
            lbl.place(relx=0.5, rely=0.65, anchor="center")
            self._tv[pos] = v
            self._tl[pos] = lbl

        self._sep()

        # ── Bottom: Fuel | TC | ABS ──────────────────────────────────────
        bot = tk.Frame(r, bg=BG)
        bot.pack(fill="x", **pad, pady=(0, 10))

        fl = tk.Frame(bot, bg=BG)
        fl.pack(side="left")
        self.v_fuel = tk.StringVar(value="Fuel  --")
        tk.Label(fl, textvariable=self.v_fuel,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="w").pack(anchor="w")
        fbg = tk.Frame(fl, bg=BG2, height=4, width=90)
        fbg.pack(anchor="w", pady=(2, 0))
        fbg.pack_propagate(False)
        self._c_fuel = tk.Canvas(fbg, bg=BG2, height=4,
                                 highlightthickness=0, bd=0)
        self._c_fuel.pack(fill="both", expand=True)

        br = tk.Frame(bot, bg=BG)
        br.pack(side="right")
        self.v_tc  = tk.StringVar(value="TC   --")
        self.v_abs = tk.StringVar(value="ABS  --")
        tk.Label(br, textvariable=self.v_tc,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="e").pack(anchor="e")
        tk.Label(br, textvariable=self.v_abs,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="e").pack(anchor="e")

    # ------------------------------------------------------------------ toggles
    def _toggle_speed(self, _=None):
        self._use_mph = not self._use_mph
        self.v_speed_unit.set("mph" if self._use_mph else "km/h")

    def _toggle_temp(self, _=None):
        self._use_f = not self._use_f
        self.v_temp_unit.set("°F" if self._use_f else "°C")

    # ------------------------------------------------------------------ drag
    def _draggable(self):
        self.root.bind("<Button-1>",
            lambda e: (setattr(self, "_ox", e.x), setattr(self, "_oy", e.y)))
        self.root.bind("<B1-Motion>",
            lambda e: self.root.geometry(
                f"+{self.root.winfo_x()+e.x-self._ox}"
                f"+{self.root.winfo_y()+e.y-self._oy}"))

    # ------------------------------------------------------------------ data thread
    def _read_loop(self):
        while self._running:
            if not self.reader.connected:
                if not self.reader.connect():
                    self._state = {"status": "waiting"}
                    time.sleep(2)
                    continue
            try:
                gfx = self.reader.read_graphics()
                phy = self.reader.read_physics()
                sta = self.reader.read_static()

                if None in (gfx, phy, sta):
                    time.sleep(0.1)
                    continue

                if gfx.status != AC_LIVE:
                    self._state = {"status": "not_live"}
                    time.sleep(0.5)
                    continue

                track, car = sta.track, sta.carModel
                if track != self._track or car != self._car:
                    self._track    = track
                    self._car      = car
                    self._max_rpm  = max(sta.maxRpm, 1000)
                    self._max_fuel = max(sta.maxFuel, 1.0)
                    self.ref.load(track, car)

                if gfx.completedLaps != self._prev_laps:
                    self._prev_laps = gfx.completedLaps
                    self.ref.load(track, car)

                self._state = {
                    "status":   "live",
                    "header":   f"{car.upper()}  ·  {track.upper()}",
                    "speed":    phy.speedKmh,
                    "gear":     phy.gear,
                    "rpm":      phy.rpm,
                    "rpm_pct":  min(phy.rpm / self._max_rpm, 1.0),
                    "laptime":  gfx.iCurrentTime,
                    "delta_ms": self.ref.delta(gfx.normalizedCarPosition,
                                               gfx.iCurrentTime),
                    "best_ms":  self.ref.best_ms,
                    "lap_num":  gfx.completedLaps + 1,
                    "position": gfx.position,
                    "tyres":    [phy.tyreTempI[i] for i in range(4)],
                    "fuel":     phy.fuel,
                    "fuel_pct": phy.fuel / self._max_fuel,
                    "tc":       phy.tc * 100,
                    "abs":      phy.abs * 100,
                }
            except Exception:
                self.reader.disconnect()
                time.sleep(1)

            time.sleep(0.1)

    # ------------------------------------------------------------------ UI tick
    def _tick(self):
        if not self._running:
            return
        s = self._state

        if not s or s.get("status") == "waiting":
            self.v_header.set("Waiting for Assetto Corsa…")
        elif s.get("status") == "not_live":
            self.v_header.set("Load a session in AC…")
        elif s.get("status") == "live":
            self.v_header.set(s["header"])

            # Speed
            spd = s["speed"] * 0.621371 if self._use_mph else s["speed"]
            self.v_speed.set(f"{spd:.0f}")

            # Gear
            g = s["gear"]
            self.v_gear.set("R" if g == 0 else ("N" if g == 1 else str(g - 1)))
            self.v_rpm.set(f"{s['rpm']:,} rpm")

            # RPM bar
            rw  = int(s["rpm_pct"] * (self.W - 56))
            col = rpm_col(s["rpm_pct"])
            self._c_rpm.delete("all")
            if rw > 0:
                self._c_rpm.create_rectangle(0, 0, rw, 8, fill=col, outline="",
                                             tags="bar")

            # Lap time
            self.v_lap.set(ms_to_laptime(s["laptime"]))

            # Delta
            dm = s["delta_ms"]
            if dm is not None:
                sign = "+" if dm >= 0 else "-"
                col  = delta_col(dm)
                self.v_delta.set(f"{sign}{abs(dm)/1000:.2f}")
                self.lbl_delta.configure(fg=col)
                bw  = min(int(abs(dm) / 25), 115)
                mid = (self.W - 24) // 2
                self._c_delta.delete("all")
                x0, x1 = (mid, mid + bw) if dm >= 0 else (mid - bw, mid)
                self._c_delta.create_rectangle(x0, 0, x1, 4,
                                               fill=col, outline="")
            else:
                self.v_delta.set("")
                self._c_delta.delete("all")

            # Best + meta
            bm = s["best_ms"]
            self.v_best.set(f"Best  {ms_to_laptime(bm) if bm else '--:--.---'}")
            pos_str = f"P{s['position']}" if s["position"] > 0 else ""
            self.v_lapnum.set(f"Lap {s['lap_num']}  {pos_str}")

            # Tyre temps
            tyre_keys = ["FL", "FR", "RL", "RR"]
            for i, pos in enumerate(tyre_keys):
                tc = s["tyres"][i]
                td = tc * 9/5 + 32 if self._use_f else tc
                self._tv[pos].set(f"{td:.0f}°")
                self._tl[pos].configure(fg=tyre_col(tc))   # colour uses °C always

            # Fuel
            fp = s["fuel_pct"]
            fw = int(max(fp, 0) * 90)
            fc = fuel_col(fp)
            self.v_fuel.set(f"Fuel  {s['fuel']:.1f}L")
            self._c_fuel.delete("all")
            if fw > 0:
                self._c_fuel.create_rectangle(0, 0, fw, 4, fill=fc, outline="")

            # TC / ABS
            self.v_tc.set(f"TC   {s['tc']:.0f}%")
            self.v_abs.set(f"ABS  {s['abs']:.0f}%")

        self.root.after(100, self._tick)

    # ------------------------------------------------------------------ quit
    def quit(self):
        self._running = False
        self.reader.disconnect()
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()


if __name__ == "__main__":
    storage.init_db()
    ACOverlay().run()
