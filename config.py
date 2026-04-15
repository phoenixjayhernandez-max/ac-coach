import os

# Load .env file if present (keeps your API key out of GitHub)
try:
    _env = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(_env):
        with open(_env) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
except Exception:
    pass

# --- PUT YOUR ANTHROPIC API KEY HERE ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE")

# Database file (stored in the same folder as this project)
DB_PATH = os.path.join(os.path.dirname(__file__), "ac_coach.db")

# How often to read telemetry from AC (in seconds). 0.1 = 10 times per second.
POLL_INTERVAL = 0.1

# AC Shared Memory names (do not change)
AC_SHM_PHYSICS  = "Local\\acpmf_physics"
AC_SHM_GRAPHICS = "Local\\acpmf_graphics"
AC_SHM_STATIC   = "Local\\acpmf_static"

# ---------------------------------------------------------------------------
# Voice coaching (pyttsx3)
# ---------------------------------------------------------------------------
# Set to False to disable all spoken feedback.
VOICE_ENABLED = True

# Words per minute for the TTS engine.
VOICE_RATE = 170

# Seconds to wait after a lap ends before speaking the AI coaching tip
# (gives you time to hear the lap time first, and clear the finish line).
VOICE_COACH_DELAY = 5
