"""
Voice coaching using Windows text-to-speech (pyttsx3).

Speaks lap times and short coaching tips aloud so you can keep your
eyes on the track instead of reading the dashboard.

Requires:  pip install pyttsx3
"""

import threading
import time
from typing import Optional

import config
from telemetry.reader import ms_to_laptime

try:
    import pyttsx3
    _TTS_AVAILABLE = True
except ImportError:
    _TTS_AVAILABLE = False
    print("[voice] pyttsx3 not installed — voice coaching disabled.")


# ---------------------------------------------------------------------------
# Core speak function
# ---------------------------------------------------------------------------

def speak(text: str):
    """Speak text in a background thread (non-blocking)."""
    if not getattr(config, "VOICE_ENABLED", True) or not _TTS_AVAILABLE:
        return

    def _run():
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate",   getattr(config, "VOICE_RATE", 175))
            engine.setProperty("volume", 0.9)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as e:
            print(f"[voice] TTS error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Lap announcement (called immediately after a lap completes)
# ---------------------------------------------------------------------------

def announce_lap(
    lap_time_ms: int,
    delta_ms: Optional[int] = None,
    is_best: bool = False,
):
    """
    Speak the lap time and delta.

    Examples:
        "Lap time: 1 colon 23 point 456. Personal best!"
        "Lap time: 1 colon 24 point 012. Plus 0.55 to best."
    """
    t = ms_to_laptime(lap_time_ms)
    # Make the time string sound natural when spoken
    spoken_time = t.replace(":", " colon ").replace(".", " point ")
    msg = f"Lap time: {spoken_time}."

    if is_best:
        msg += " Personal best!"
    elif delta_ms is not None:
        sign = "plus" if delta_ms >= 0 else "minus"
        secs = abs(delta_ms) / 1000.0
        msg += f" {sign} {secs:.2f} to best."

    speak(msg)


# ---------------------------------------------------------------------------
# Real-time warning checks (call from the collector's poll loop)
# ---------------------------------------------------------------------------

# Cooldown tracker so we don't spam warnings
_last_warned: dict = {}
_WARN_COOLDOWN = 15   # seconds between same warning type


def _cooldown_ok(key: str) -> bool:
    now = time.time()
    if now - _last_warned.get(key, 0) >= _WARN_COOLDOWN:
        _last_warned[key] = now
        return True
    return False


def check_warnings(phy) -> None:
    """
    Check live physics for dangerous conditions and speak a warning.
    Pass a SPageFilePhysics struct from the telemetry reader.
    """
    # Brake temperature warnings (FL / FR — fronts matter most)
    for i, pos in enumerate(["front left", "front right"]):
        temp = phy.brakeTemp[i]
        if temp > 850 and _cooldown_ok(f"brake_{i}"):
            speak(f"{pos} brakes at {int(temp)} degrees.")

    # Tyre temperature warnings
    tyre_names = ["front left", "front right", "rear left", "rear right"]
    for i, name in enumerate(tyre_names):
        temp = phy.tyreTempI[i]
        if temp > 110 and _cooldown_ok(f"tyre_{i}"):
            speak(f"{name} tyre overheating at {int(temp)} degrees.")


# ---------------------------------------------------------------------------
# Post-lap AI coaching tip (background, fires a few seconds after lap end)
# ---------------------------------------------------------------------------

def post_lap_coaching_async(lap_id: int, session_id: int):
    """
    After VOICE_COACH_DELAY seconds, generate a short AI tip and speak it.
    Runs in a background daemon thread — never blocks the collector.
    """
    def _run():
        try:
            delay = getattr(config, "VOICE_COACH_DELAY", 4)
            time.sleep(delay)

            from coaching import ai_coach
            tip = ai_coach.quick_tip(lap_id, session_id)
            if tip:
                speak(tip)
        except Exception as e:
            print(f"[voice] Coaching tip error: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
