import os

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
