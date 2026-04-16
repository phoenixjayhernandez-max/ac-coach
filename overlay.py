"""
AC Coach — Live On-Screen Overlay.
Drag with left-click. Press Q to quit.
Click km/h or °C labels to toggle units.
Auto-starts the telemetry collector on launch.
"""

import os
import sys
import subprocess
import tkinter as tk
import threading
import time
import ctypes
import math
from typing import Optional, Dict

import config
from telemetry.reader import ACTelemetryReader, AC_LIVE, ms_to_laptime
from database import storage


# ---------------------------------------------------------------------------
# Fix DPI scaling (removes graininess on Windows)
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
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
PURPLE  = "#bb86fc"

FONT_NUM = ("Consolas",  )
FONT_UI  = ("Segoe UI",  )

SESSION_NAMES = {
    0: "",
    1: "PRACTICE",
    2: "QUALIFY",
    3: "RACE",
    4: "HOTLAP",
    5: "TIME ATK",
    6: "DRIFT",
    7: "DRAG",
    8: "SUPERPOLE",
}

SESSION_COLORS = {
    1: DIM,
    2: BLUE,
    3: RED,
    4: ORANGE,
    5: PURPLE,
    6: GREEN,
    7: GREEN,
    8: ACCENT,
}


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

def brake_col(t):
    if t <= 0:    return DIM
    elif t < 200: return BLUE
    elif t < 350: return GREEN
    elif t < 550: return ORANGE
    else:         return RED

def fuel_col(p):
    return GREEN if p > 0.3 else (ORANGE if p > 0.15 else RED)


# ---------------------------------------------------------------------------
# Reference lap
# ---------------------------------------------------------------------------
class ReferenceLap:
    def __init__(self):
        self._lk    = {}
        self._best  = 0
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
    W, H   = 290, 720
    TW, TH = 56, 66    # tyre canvas
    BTW    = 56         # brake temp cell width (height is text-only)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AC Coach")
        self.root.configure(bg=BG)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.95)
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.W}x{self.H}+20+20")

        self._use_mph = False
        self._use_f   = False

        self._build()
        self._draggable()
        self.root.after(600,  self._pin)
        self.root.after(2000, self._keep_pinned)

        self.reader = ACTelemetryReader()
        self.ref    = ReferenceLap()
        self._running    = True
        self._max_rpm    = 8000
        self._max_fuel   = 90.0
        self._track      = ""
        self._car        = ""
        self._prev_laps  = -1
        self._state: dict = {}

        # Sector tracking
        self._prev_sector_idx = -1
        self._cur_s1: Optional[int] = None
        self._cur_s2: Optional[int] = None
        self._best_s1: int = 0
        self._best_s2: int = 0
        self._best_s3: int = 0

        threading.Thread(target=self._read_loop, daemon=True).start()
        self.root.after(100, self._tick)
        self.root.bind("<KeyPress-q>", lambda _: self.quit())
        self.root.bind("<KeyPress-Q>", lambda _: self.quit())

        # Auto-start collector
        self._collector_proc: Optional[subprocess.Popen] = None
        self._start_collector()

    # ------------------------------------------------------------------ collector
    def _start_collector(self):
        try:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collector.py")
            if not os.path.exists(script):
                return
            self._collector_proc = subprocess.Popen(
                [sys.executable, script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
            print(f"[overlay] Collector auto-started (PID {self._collector_proc.pid})")
        except Exception as e:
            print(f"[overlay] Could not auto-start collector: {e}")

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

    # ------------------------------------------------------------------ UI build
    def _sep(self, pady=4):
        tk.Frame(self.root, bg=BG3, height=1).pack(fill="x", padx=10, pady=pady)

    def _build(self):
        r   = self.root
        pad = dict(padx=12)

        # ── Header ───────────────────────────────────────────────────────
        self.v_header = tk.StringVar(value="Waiting for Assetto Corsa…")
        tk.Label(r, textvariable=self.v_header,
                 font=(*FONT_UI, 8), bg=BG, fg=ACCENT,
                 anchor="center").pack(fill="x", **pad, pady=(8, 1))

        # Session badge (RACE / QUALIFY / etc.)
        self.v_session_badge = tk.StringVar(value="")
        self.lbl_badge = tk.Label(r, textvariable=self.v_session_badge,
                 font=(*FONT_UI, 7, "bold"), bg=BG, fg=DIM,
                 anchor="center")
        self.lbl_badge.pack(fill="x", **pad, pady=(0, 2))

        self._sep(pady=2)

        # ── Speed + Gear ──────────────────────────────────────────────────
        spd_row = tk.Frame(r, bg=BG)
        spd_row.pack(fill="x", **pad, pady=(4, 0))

        self.v_gear = tk.StringVar(value="N")
        tk.Label(spd_row, textvariable=self.v_gear,
                 font=(*FONT_NUM, 28, "bold"), bg=BG, fg=ACCENT,
                 anchor="w", width=2).pack(side="left")

        self.v_speed = tk.StringVar(value="0")
        tk.Label(spd_row, textvariable=self.v_speed,
                 font=(*FONT_NUM, 28, "bold"), bg=BG, fg=FG,
                 anchor="e").pack(side="left", padx=(6, 0))

        self.v_speed_unit = tk.StringVar(value="km/h")
        spd_unit_lbl = tk.Label(spd_row, textvariable=self.v_speed_unit,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM,
                 cursor="hand2", anchor="sw")
        spd_unit_lbl.pack(side="left", padx=(4, 0), pady=(10, 0))
        spd_unit_lbl.bind("<Button-1>", self._toggle_speed)

        # ── RPM bar ───────────────────────────────────────────────────────
        rpm_row = tk.Frame(r, bg=BG)
        rpm_row.pack(fill="x", **pad, pady=(2, 0))
        self.v_rpm = tk.StringVar(value="")
        tk.Label(rpm_row, textvariable=self.v_rpm,
                 font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 anchor="e").pack(fill="x")

        rpm_bg = tk.Frame(r, bg=BG2, height=8)
        rpm_bg.pack(fill="x", padx=12, pady=(1, 0))
        rpm_bg.pack_propagate(False)
        self._c_rpm = tk.Canvas(rpm_bg, bg=BG2, height=8,
                                highlightthickness=0, bd=0)
        self._c_rpm.pack(fill="both", expand=True)

        self._sep(pady=3)

        # ── Lap time + Delta ──────────────────────────────────────────────
        lt = tk.Frame(r, bg=BG)
        lt.pack(fill="x", **pad)

        self.v_lap = tk.StringVar(value="-:--.---")
        tk.Label(lt, textvariable=self.v_lap,
                 font=(*FONT_NUM, 14, "bold"), bg=BG, fg=FG,
                 anchor="w").pack(side="left")

        self.v_delta = tk.StringVar(value="")
        self.lbl_delta = tk.Label(lt, textvariable=self.v_delta,
                 font=(*FONT_NUM, 14, "bold"), bg=BG, fg=GREEN, anchor="e")
        self.lbl_delta.pack(side="right")

        # Delta bar
        dbar = tk.Frame(r, bg=BG2, height=4)
        dbar.pack(fill="x", padx=12, pady=(2, 0))
        dbar.pack_propagate(False)
        self._c_delta = tk.Canvas(dbar, bg=BG2, height=4,
                                  highlightthickness=0, bd=0)
        self._c_delta.pack(fill="both", expand=True)

        # Best + Lap#
        meta = tk.Frame(r, bg=BG)
        meta.pack(fill="x", **pad, pady=(3, 1))
        self.v_best = tk.StringVar(value="Best  --:--.---")
        tk.Label(meta, textvariable=self.v_best,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="w").pack(side="left")
        self.v_lapnum = tk.StringVar(value="")
        tk.Label(meta, textvariable=self.v_lapnum,
                 font=(*FONT_UI, 8), bg=BG, fg=DIM, anchor="e").pack(side="right")

        # ── Sector times ──────────────────────────────────────────────────
        sect_head = tk.Frame(r, bg=BG)
        sect_head.pack(fill="x", **pad, pady=(4, 2))
        tk.Label(sect_head, text="SECTORS",
                 font=(*FONT_UI, 7), bg=BG, fg=DIM, anchor="w").pack(side="left")

        # Row 1: S1 + S2
        s12_row = tk.Frame(r, bg=BG)
        s12_row.pack(fill="x", **pad)

        # S1
        s1_f = tk.Frame(s12_row, bg=BG)
        s1_f.pack(side="left", expand=True, fill="x")
        tk.Label(s1_f, text="S1", font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 anchor="w").pack(side="left")
        self.v_s1 = tk.StringVar(value="--:--.---")
        self.lbl_s1 = tk.Label(s1_f, textvariable=self.v_s1,
                 font=(*FONT_NUM, 9), bg=BG, fg=DIM, anchor="w")
        self.lbl_s1.pack(side="left", padx=(4, 0))
        self.v_s1d = tk.StringVar(value="")
        self.lbl_s1d = tk.Label(s1_f, textvariable=self.v_s1d,
                 font=(*FONT_NUM, 8), bg=BG, fg=DIM, anchor="w")
        self.lbl_s1d.pack(side="left", padx=(3, 0))

        # S2
        s2_f = tk.Frame(s12_row, bg=BG)
        s2_f.pack(side="right", expand=True, fill="x")
        tk.Label(s2_f, text="S2", font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 anchor="w").pack(side="left")
        self.v_s2 = tk.StringVar(value="--:--.---")
        self.lbl_s2 = tk.Label(s2_f, textvariable=self.v_s2,
                 font=(*FONT_NUM, 9), bg=BG, fg=DIM, anchor="w")
        self.lbl_s2.pack(side="left", padx=(4, 0))
        self.v_s2d = tk.StringVar(value="")
        self.lbl_s2d = tk.Label(s2_f, textvariable=self.v_s2d,
                 font=(*FONT_NUM, 8), bg=BG, fg=DIM, anchor="w")
        self.lbl_s2d.pack(side="left", padx=(3, 0))

        # Row 2: S3 (centred)
        s3_row = tk.Frame(r, bg=BG)
        s3_row.pack(fill="x", **pad, pady=(2, 0))
        tk.Label(s3_row, text="S3", font=(*FONT_UI, 7), bg=BG, fg=DIM,
                 anchor="w").pack(side="left")
        self.v_s3 = tk.StringVar(value="--:--.---")
        self.lbl_s3 = tk.Label(s3_row, textvariable=self.v_s3,
                 font=(*FONT_NUM, 9), bg=BG, fg=DIM, anchor="w")
        self.lbl_s3.pack(side="left", padx=(4, 0))
        self.v_s3d = tk.StringVar(value="")
        self.lbl_s3d = tk.Label(s3_row, textvariable=self.v_s3d,
                 font=(*FONT_NUM, 8), bg=BG, fg=DIM, anchor="w")
        self.lbl_s3d.pack(side="left", padx=(3, 0))

        self._sep()

        # ── Tyre temps ────────────────────────────────────────────────────
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
        tgrid.pack(pady=(4, 0))

        self._tyre_c: Dict[str, tk.Canvas] = {}
        for pos, row, col in [("FL",0,0),("FR",0,1),("RL",1,0),("RR",1,1)]:
            c = tk.Canvas(tgrid, bg=BG, width=self.TW, height=self.TH,
                          highlightthickness=0, bd=0)
            c.grid(row=row, column=col, padx=8, pady=4)
            self._tyre_c[pos] = c
            self._draw_tyre(c, pos, 0)

        self._sep()

        # ── Brake temps ───────────────────────────────────────────────────
        b_head = tk.Frame(r, bg=BG)
        b_head.pack(fill="x", **pad, pady=(0, 4))
        tk.Label(b_head, text="BRAKES",
                 font=(*FONT_UI, 7), bg=BG, fg=DIM, anchor="w").pack(side="left")

        bgrid = tk.Frame(r, bg=BG)
        bgrid.pack(pady=(0, 2))

        self._brake_c: Dict[str, tk.Canvas] = {}
        for pos, row, col in [("FL",0,0),("FR",0,1),("RL",1,0),("RR",1,1)]:
            c = tk.Canvas(bgrid, bg=BG, width=self.BTW, height=40,
                          highlightthickness=0, bd=0)
            c.grid(row=row, column=col, padx=8, pady=2)
            self._brake_c[pos] = c
            self._draw_brake(c, pos, 0)

        self._sep()

        # ── Bottom: Fuel | TC | ABS ───────────────────────────────────────
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

    # ------------------------------------------------------------------ tyre drawing
    def _draw_tyre(self, canvas: tk.Canvas, pos: str, temp_c: float):
        canvas.delete("all")
        W, H = self.TW, self.TH
        cx   = W // 2
        col  = tyre_col(temp_c)

        canvas.create_text(cx, 4, text=pos, fill=DIM,
                           font=(*FONT_UI, 7), anchor="n")

        t0x, t0y = 4, 13
        t1x, t1y = W - 4, H - 16

        if temp_c > 95:
            canvas.create_oval(t0x-3, t0y-3, t1x+3, t1y+3,
                               fill=col, outline="", stipple="gray25")

        canvas.create_oval(t0x, t0y, t1x, t1y, fill=col, outline="")
        canvas.create_arc(t0x+3, t0y+3, t1x-3, t1y-3,
                          start=120, extent=60,
                          outline="#ffffff", style="arc", width=1)

        inset = 11
        canvas.create_oval(t0x+inset, t0y+inset, t1x-inset, t1y-inset,
                           fill="#1a1a1a", outline="#2e2e2e", width=1)

        rcx = (t0x + t1x) // 2
        rcy = (t0y + t1y) // 2
        for i in range(5):
            angle = math.radians(i * 72 - 90)
            bx = rcx + 7 * math.cos(angle)
            by = rcy + 7 * math.sin(angle)
            canvas.create_oval(bx-2, by-2, bx+2, by+2,
                               fill="#303030", outline="")
        canvas.create_oval(rcx-2, rcy-2, rcx+2, rcy+2,
                           fill="#404040", outline="")

        if temp_c > 0:
            t = temp_c * 9/5 + 32 if self._use_f else temp_c
            canvas.create_text(cx, H-3, text=f"{t:.0f}°",
                               fill=col, font=(*FONT_NUM, 9, "bold"), anchor="s")
        else:
            canvas.create_text(cx, H-3, text="--°",
                               fill=DIM, font=(*FONT_NUM, 9), anchor="s")

    # ------------------------------------------------------------------ brake temp drawing
    def _draw_brake(self, canvas: tk.Canvas, pos: str, temp_c: float):
        canvas.delete("all")
        W, H = self.BTW, 40
        cx   = W // 2
        col  = brake_col(temp_c)

        canvas.create_text(cx, 4, text=pos, fill=DIM,
                           font=(*FONT_UI, 7), anchor="n")

        # Simple coloured disc representing the brake disc
        bx0, by0, bx1, by1 = 8, 12, W-8, H-8
        canvas.create_oval(bx0, by0, bx1, by1, fill=col, outline="")
        # Inner hole
        cx2 = (bx0+bx1)//2
        cy2 = (by0+by1)//2
        canvas.create_oval(cx2-5, cy2-5, cx2+5, cy2+5,
                           fill="#1a1a1a", outline="#2e2e2e", width=1)

        if temp_c > 0:
            t = temp_c * 9/5 + 32 if self._use_f else temp_c
            canvas.create_text(cx, H-1, text=f"{t:.0f}°",
                               fill=col, font=(*FONT_NUM, 8, "bold"), anchor="s")
        else:
            canvas.create_text(cx, H-1, text="--°",
                               fill=DIM, font=(*FONT_NUM, 8), anchor="s")

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
                    # Reset sector bests on track/car change
                    self._best_s1 = self._best_s2 = self._best_s3 = 0
                    self._cur_s1  = self._cur_s2  = None
                    self._prev_sector_idx = -1

                # -- Sector tracking --
                cur_sector = gfx.currentSectorIndex
                if cur_sector != self._prev_sector_idx:
                    if cur_sector == 1 and self._prev_sector_idx == 0:
                        self._cur_s1 = gfx.lastSectorTime
                    elif cur_sector == 2 and self._prev_sector_idx == 1:
                        self._cur_s2 = gfx.lastSectorTime
                    self._prev_sector_idx = cur_sector

                # -- Lap completion --
                if gfx.completedLaps != self._prev_laps:
                    self._prev_laps = gfx.completedLaps
                    self.ref.load(track, car)

                    # Capture S3 = last sector at lap end, update bests
                    s3 = gfx.lastSectorTime if gfx.lastSectorTime > 0 else None
                    if self._cur_s1 and (self._best_s1 == 0 or self._cur_s1 < self._best_s1):
                        self._best_s1 = self._cur_s1
                    if self._cur_s2 and (self._best_s2 == 0 or self._cur_s2 < self._best_s2):
                        self._best_s2 = self._cur_s2
                    if s3 and (self._best_s3 == 0 or s3 < self._best_s3):
                        self._best_s3 = s3

                    # Reset for next lap
                    self._cur_s1 = None
                    self._cur_s2 = None
                    self._prev_sector_idx = -1

                self._state = {
                    "status":       "live",
                    "header":       f"{car.upper()}  ·  {track.upper()}",
                    "session_type": gfx.session,
                    "speed":        phy.speedKmh,
                    "gear":         phy.gear,
                    "rpm":          phy.rpm,
                    "rpm_pct":      min(phy.rpm / self._max_rpm, 1.0),
                    "laptime":      gfx.iCurrentTime,
                    "delta_ms":     self.ref.delta(gfx.normalizedCarPosition,
                                                   gfx.iCurrentTime),
                    "best_ms":      self.ref.best_ms,
                    "lap_num":      gfx.completedLaps + 1,
                    "position":     gfx.position,
                    # Sectors
                    "s1_ms":        self._cur_s1,
                    "s2_ms":        self._cur_s2,
                    "best_s1":      self._best_s1,
                    "best_s2":      self._best_s2,
                    "best_s3":      self._best_s3,
                    # Temps
                    "tyres":        [phy.tyreTempI[i]  for i in range(4)],
                    "brakes":       [phy.brakeTemp[i]  for i in range(4)],
                    "fuel":         phy.fuel,
                    "fuel_pct":     phy.fuel / self._max_fuel,
                    "tc":           phy.tc  * 100,
                    "abs":          phy.abs * 100,
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
            self.v_session_badge.set("")
        elif s.get("status") == "not_live":
            self.v_header.set("Load a session in AC…")
            self.v_session_badge.set("")
        elif s.get("status") == "live":
            self.v_header.set(s["header"])

            # Session badge
            stype = s.get("session_type", 0)
            badge = SESSION_NAMES.get(stype, "")
            self.v_session_badge.set(badge)
            self.lbl_badge.configure(fg=SESSION_COLORS.get(stype, DIM))

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
                self._c_rpm.create_rectangle(0, 0, rw, 8, fill=col, outline="")

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
                x0, x1 = (mid, mid+bw) if dm >= 0 else (mid-bw, mid)
                self._c_delta.create_rectangle(x0, 0, x1, 4, fill=col, outline="")
            else:
                self.v_delta.set("")
                self._c_delta.delete("all")

            # Best + meta
            bm = s["best_ms"]
            self.v_best.set(f"Best  {ms_to_laptime(bm) if bm else '--:--.---'}")
            pos_str = f"P{s['position']}" if s["position"] > 0 else ""
            self.v_lapnum.set(f"Lap {s['lap_num']}  {pos_str}")

            # Sectors
            def _fmt_sector(ms, best_ms):
                """Returns (time_str, delta_str, delta_color)"""
                if not ms:
                    return "--:--.---", "", DIM
                t_str = ms_to_laptime(ms)
                if best_ms and best_ms > 0:
                    diff = ms - best_ms
                    sign = "+" if diff >= 0 else "-"
                    d_str = f"{sign}{abs(diff)/1000:.3f}"
                    d_col = GREEN if diff < 0 else (ACCENT if diff == 0 else RED)
                    return t_str, d_str, d_col
                return t_str, "", FG

            s1t, s1d, s1c = _fmt_sector(s.get("s1_ms"), s.get("best_s1"))
            s2t, s2d, s2c = _fmt_sector(s.get("s2_ms"), s.get("best_s2"))

            self.v_s1.set(s1t)
            self.lbl_s1.configure(fg=FG if s.get("s1_ms") else DIM)
            self.v_s1d.set(s1d)
            self.lbl_s1d.configure(fg=s1c)

            self.v_s2.set(s2t)
            self.lbl_s2.configure(fg=FG if s.get("s2_ms") else DIM)
            self.v_s2d.set(s2d)
            self.lbl_s2d.configure(fg=s2c)

            # S3 shows best from previous laps (it only completes at lap end)
            bs3 = s.get("best_s3", 0)
            if bs3:
                self.v_s3.set(ms_to_laptime(bs3))
                self.lbl_s3.configure(fg=DIM)
                self.v_s3d.set("best")
                self.lbl_s3d.configure(fg=DIM)
            else:
                self.v_s3.set("--:--.---")
                self.lbl_s3.configure(fg=DIM)
                self.v_s3d.set("")

            # Tyre icons
            for i, pos in enumerate(["FL", "FR", "RL", "RR"]):
                self._draw_tyre(self._tyre_c[pos], pos, s["tyres"][i])

            # Brake temp icons
            for i, pos in enumerate(["FL", "FR", "RL", "RR"]):
                self._draw_brake(self._brake_c[pos], pos, s["brakes"][i])

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
        if self._collector_proc and self._collector_proc.poll() is None:
            self._collector_proc.terminate()
            print("[overlay] Collector stopped.")
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()


if __name__ == "__main__":
    storage.init_db()
    ACOverlay().run()
