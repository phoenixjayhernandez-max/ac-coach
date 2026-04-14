"""
AI coaching engine powered by Claude.
Analyzes your lap data and driving telemetry to give personalized feedback
and setup recommendations — like having a real race engineer in your ear.
"""

from typing import Optional
import anthropic
import config
from database import storage
from telemetry.reader import ms_to_laptime


def _build_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Lap analysis coaching
# ---------------------------------------------------------------------------

def analyze_lap(lap_id: int, session_id: int) -> str:
    """
    Sends lap data + telemetry summary to Claude and returns coaching feedback.
    Returns a string you can display directly in the dashboard.
    """
    lap = _get_lap_context(lap_id, session_id)
    if not lap:
        return "No lap data found."

    prompt = _build_lap_prompt(lap)
    client = _build_client()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_COACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def compare_laps(lap_id_a: int, lap_id_b: int, session_id: int) -> str:
    """Compare two laps — useful for spotting where time was gained or lost."""
    lap_a = _get_lap_context(lap_id_a, session_id)
    lap_b = _get_lap_context(lap_id_b, session_id)
    if not lap_a or not lap_b:
        return "Could not load both laps for comparison."

    prompt = (
        f"Compare these two laps and tell me specifically where time was gained or lost.\n\n"
        f"**LAP A (Reference / Faster)**\n{_format_lap(lap_a)}\n\n"
        f"**LAP B (Target / Slower)**\n{_format_lap(lap_b)}\n\n"
        "Focus on: sector differences, driving style differences, tyre/brake temperatures, "
        "and the single biggest area for improvement in Lap B."
    )
    client = _build_client()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_COACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Setup recommendations
# ---------------------------------------------------------------------------

def get_setup_advice(session_id: int) -> str:
    """
    Looks at your last several laps, identifies patterns in the driving data,
    and suggests setup changes that would help you specifically.
    """
    laps = storage.get_laps(session_id)
    if not laps:
        return "No laps recorded yet. Complete a few laps first."

    # Summarize the session data
    lap_summaries = []
    for lap in laps[-10:]:   # last 10 laps
        tele = storage.get_telemetry(lap["id"])
        summary = _summarize_telemetry(tele) if tele else {}
        lap_summaries.append({**lap, **summary})

    prompt = _build_setup_prompt(lap_summaries)
    client = _build_client()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=_COACH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Interactive chat — user can ask follow-up questions
# ---------------------------------------------------------------------------

def chat(conversation_history: list, user_message: str, session_id: int) -> str:
    """
    Maintains a conversation with Claude about your driving.
    Pass the full conversation_history list each time so Claude remembers context.
    """
    # Build context from recent laps to ground the conversation
    laps = storage.get_laps(session_id)
    context_block = ""
    if laps:
        best = min(laps, key=lambda x: x["lap_time_ms"] or 999999)
        last = laps[-1]
        context_block = (
            f"\n\n[Session context: {best.get('track','?')} in a {best.get('car','?')}. "
            f"Best lap: {ms_to_laptime(best['lap_time_ms'])}. "
            f"Last lap: {ms_to_laptime(last['lap_time_ms'])}. "
            f"{len(laps)} laps total this session.]"
        )

    history = conversation_history + [
        {"role": "user", "content": user_message + context_block}
    ]

    client = _build_client()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=_COACH_SYSTEM_PROMPT,
        messages=history,
    )
    return message.content[0].text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_lap_context(lap_id: int, session_id: int) -> Optional[dict]:
    laps = storage.get_laps(session_id)
    lap = next((l for l in laps if l["id"] == lap_id), None)
    if not lap:
        return None
    tele = storage.get_telemetry(lap_id)
    summary = _summarize_telemetry(tele) if tele else {}
    return {**lap, **summary}


def _summarize_telemetry(tele: list) -> dict:
    """Condense thousands of telemetry samples into key averages."""
    if not tele:
        return {}
    speeds       = [t["speed_kmh"] for t in tele]
    throttles    = [t["throttle"]  for t in tele]
    brakes       = [t["brake"]     for t in tele]
    steer        = [abs(t["steer_angle"]) for t in tele]
    tyre_fl      = [t["tyre_temp_fl"] for t in tele if t["tyre_temp_fl"]]
    tyre_fr      = [t["tyre_temp_fr"] for t in tele if t["tyre_temp_fr"]]
    tyre_rl      = [t["tyre_temp_rl"] for t in tele if t["tyre_temp_rl"]]
    tyre_rr      = [t["tyre_temp_rr"] for t in tele if t["tyre_temp_rr"]]
    brake_fl     = [t["brake_temp_fl"] for t in tele if t["brake_temp_fl"]]
    brake_fr     = [t["brake_temp_fr"] for t in tele if t["brake_temp_fr"]]

    def avg(lst): return round(sum(lst) / len(lst), 2) if lst else 0

    return {
        "tele_max_speed":    round(max(speeds), 1),
        "tele_avg_throttle": avg(throttles),
        "tele_avg_brake":    avg(brakes),
        "tele_avg_steer":    avg(steer),
        "tele_avg_tyre_fl":  avg(tyre_fl),
        "tele_avg_tyre_fr":  avg(tyre_fr),
        "tele_avg_tyre_rl":  avg(tyre_rl),
        "tele_avg_tyre_rr":  avg(tyre_rr),
        "tele_avg_brake_fl": avg(brake_fl),
        "tele_avg_brake_fr": avg(brake_fr),
    }


def _format_lap(lap: dict) -> str:
    return (
        f"Lap {lap.get('lap_number','?')}  |  "
        f"Time: {ms_to_laptime(lap.get('lap_time_ms',0))}  |  "
        f"S1: {ms_to_laptime(lap.get('sector1_ms',0))}  "
        f"S2: {ms_to_laptime(lap.get('sector2_ms',0))}  "
        f"S3: {ms_to_laptime(lap.get('sector3_ms',0))}\n"
        f"Max speed: {lap.get('tele_max_speed', lap.get('max_speed_kmh','?'))} km/h  |  "
        f"Avg throttle: {lap.get('tele_avg_throttle', lap.get('avg_throttle','?'))}  |  "
        f"Avg brake: {lap.get('tele_avg_brake', lap.get('avg_brake','?'))}\n"
        f"Tyre temps (FL/FR/RL/RR): "
        f"{lap.get('tele_avg_tyre_fl','?')} / {lap.get('tele_avg_tyre_fr','?')} / "
        f"{lap.get('tele_avg_tyre_rl','?')} / {lap.get('tele_avg_tyre_rr','?')} °C\n"
        f"Brake temps (FL/FR): {lap.get('tele_avg_brake_fl','?')} / {lap.get('tele_avg_brake_fr','?')} °C\n"
        f"Tyre compound: {lap.get('tyre_compound','?')}  |  "
        f"Air: {lap.get('air_temp','?')}°C  Road: {lap.get('road_temp','?')}°C"
    )


def _build_lap_prompt(lap: dict) -> str:
    return (
        f"Analyze this lap and give me specific, actionable coaching feedback.\n\n"
        f"**Track:** {lap.get('track','?')}\n"
        f"**Car:** {lap.get('car','?')}\n\n"
        f"{_format_lap(lap)}\n\n"
        "Please cover:\n"
        "1. Where I'm likely losing the most time\n"
        "2. What my tyre and brake temperature data tells you about my driving style\n"
        "3. One specific thing to focus on next lap\n"
        "Keep it practical — I'm a developing sim racer, not an F1 pro."
    )


def _build_setup_prompt(laps: list) -> str:
    summary_lines = "\n".join(_format_lap(l) for l in laps)
    car  = laps[0].get("car", "?") if laps else "?"
    track = laps[0].get("track", "?") if laps else "?"
    return (
        f"Based on my last {len(laps)} laps at **{track}** in a **{car}**, "
        f"suggest specific setup changes that would help me.\n\n"
        f"{summary_lines}\n\n"
        "Look for patterns across the laps — tyre temperature imbalances, "
        "brake bias issues, signs of under/oversteer — and give concrete setup adjustments "
        "(e.g. 'increase front ARB by 2 clicks', 'move brake bias 1% rearward'). "
        "Explain WHY each change would help based on the data."
    )


_COACH_SYSTEM_PROMPT = """You are an expert sim racing coach and race engineer with deep knowledge of
Assetto Corsa, car setup, and high-performance driving technique. You have the analytical
mindset of a Formula 1 data engineer combined with the teaching ability of a patient coach.

When analyzing data:
- Be specific and data-driven, not generic
- Give actionable advice the driver can apply immediately
- Explain the 'why' behind each recommendation
- Reference the actual numbers from the telemetry
- Be encouraging but honest about areas for improvement
- Keep responses focused and practical — avoid padding

When giving setup advice:
- Reference specific setup parameters (toe, camber, ARB, dampers, brake bias, etc.)
- Explain how each change affects the car's balance
- Prioritize the most impactful changes first
- Acknowledge that setup is a trade-off
"""

