"""
AI coaching engine — supports Claude, Ollama (local/free), and Gemini.
Switch providers by setting AI_PROVIDER in your .env file.
"""

from typing import Optional
import config
from database import storage
from telemetry.reader import ms_to_laptime


# ---------------------------------------------------------------------------
# Provider dispatcher — all AI calls go through here
# ---------------------------------------------------------------------------

def _call_ai(system_prompt: str, messages: list, max_tokens: int = 1024) -> str:
    """
    Route an AI request to whichever provider is configured.
    `messages` is a list of {"role": "user"/"assistant", "content": "..."} dicts.
    Returns the assistant's reply as a plain string.
    """
    provider = config.AI_PROVIDER.lower()

    if provider == "claude":
        return _call_claude(system_prompt, messages, max_tokens)
    elif provider == "gemini":
        return _call_gemini(system_prompt, messages, max_tokens)
    else:
        return _call_ollama(system_prompt, messages, max_tokens)


def _call_claude(system_prompt: str, messages: list, max_tokens: int) -> str:
    import anthropic
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
    except Exception as e:
        err = str(e).lower()
        # Fall back to Gemini if credits exhausted or auth fails
        if any(k in err for k in ("credit", "billing", "quota", "permission", "401", "403", "529")):
            if config.GEMINI_API_KEY:
                print(f"[ai_coach] Claude error ({e}) — falling back to Gemini")
                return _call_gemini(system_prompt, messages, max_tokens)
        return f"Claude API error: {e}"


def _call_ollama(system_prompt: str, messages: list, max_tokens: int) -> str:
    import requests

    # Build full message list with system prompt
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    # Try /api/chat first (Ollama >= 0.1.14)
    try:
        r = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": config.OLLAMA_MODEL,
                "messages": full_messages,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=120,
        )
        if r.status_code == 404:
            raise ValueError("chat_not_supported")
        r.raise_for_status()
        return r.json()["message"]["content"]

    except requests.exceptions.ConnectionError:
        return (
            "Ollama is not running. Start it with: ollama serve\n"
            "Then pull a model: ollama pull llama3.1"
        )
    except ValueError:
        pass  # fall through to /api/generate
    except Exception as e:
        return f"Ollama error: {e}"

    # Fallback: /api/generate (older Ollama versions)
    try:
        prompt_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in full_messages
        )
        r = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt_text,
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["response"]
    except requests.exceptions.ConnectionError:
        return (
            "Ollama is not running. Start it with: ollama serve\n"
            "Then pull a model: ollama pull llama3.1"
        )
    except Exception as e:
        return f"Ollama error: {e}"


def _call_gemini(system_prompt: str, messages: list, max_tokens: int) -> str:
    try:
        import google.generativeai as genai
    except ImportError:
        return "Gemini not installed. Run: pip install google-generativeai"

    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        config.GEMINI_MODEL,
        system_instruction=system_prompt,
        generation_config={"max_output_tokens": max_tokens},
    )

    # Convert message history to Gemini format
    history = []
    for m in messages[:-1]:
        history.append({
            "role": "user" if m["role"] == "user" else "model",
            "parts": [m["content"]],
        })

    chat = model.start_chat(history=history)
    last_msg = messages[-1]["content"] if messages else ""
    response = chat.send_message(last_msg)
    return response.text


# ---------------------------------------------------------------------------
# Lap analysis coaching
# ---------------------------------------------------------------------------

def analyze_lap(lap_id: int, session_id: int) -> str:
    lap = _get_lap_context(lap_id, session_id)
    if not lap:
        return "No lap data found."
    prompt = _build_lap_prompt(lap)
    return _call_ai(_COACH_SYSTEM_PROMPT, [{"role": "user", "content": prompt}], max_tokens=1024)


def compare_laps(lap_id_a: int, lap_id_b: int, session_id: int) -> str:
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
    return _call_ai(_COACH_SYSTEM_PROMPT, [{"role": "user", "content": prompt}], max_tokens=1024)


def quick_tip(lap_id: int, session_id: int) -> str:
    """Short spoken tip — 2 sentences, no markdown."""
    lap = _get_lap_context(lap_id, session_id)
    if not lap:
        return ""

    # Skip if Claude is selected but has no key
    if config.AI_PROVIDER == "claude" and (
        not config.ANTHROPIC_API_KEY or config.ANTHROPIC_API_KEY == "YOUR_API_KEY_HERE"
    ):
        return ""

    prompt = (
        f"One short coaching tip (2 sentences, spoken aloud — no bullet points or markdown) "
        f"for this lap at {lap.get('track', '?')} in a {lap.get('car', '?')}.\n"
        f"Lap time: {ms_to_laptime(lap.get('lap_time_ms', 0))}\n"
        f"{_format_lap(lap)}\n\n"
        "Be direct and specific. Focus on the single biggest area for improvement."
    )
    system = (
        "You are a sim racing coach giving brief spoken post-lap feedback. "
        "No markdown, no bullet points. Two sentences maximum. Be specific."
    )
    return _call_ai(system, [{"role": "user", "content": prompt}], max_tokens=80)


def analyze_corners(lap_id: int, session_id: int) -> str:
    from coaching.corner_analysis import detect_corners, format_corners_for_prompt

    lap = _get_lap_context(lap_id, session_id)
    if not lap:
        return "No lap data found."

    tele = storage.get_telemetry(lap_id)
    if not tele:
        return "No telemetry data for this lap."

    corners = detect_corners(tele)
    if not corners:
        return (
            "No corners detected. This usually means the telemetry for this lap "
            "was recorded before the normalized position field was added — "
            "record a new lap and try again."
        )

    corner_text = format_corners_for_prompt(corners, lap.get("car", "?"), lap.get("track", "?"))
    prompt = (
        f"Analyze this corner-by-corner data and tell me where I'm losing the most time.\n\n"
        f"{corner_text}\n\n"
        f"Overall lap time: {ms_to_laptime(lap.get('lap_time_ms', 0))}\n\n"
        "For the top 3 corners where I can gain the most time, give a specific actionable fix. "
        "Reference the actual entry/exit speeds and G-forces from the data."
    )
    return _call_ai(_COACH_SYSTEM_PROMPT, [{"role": "user", "content": prompt}], max_tokens=1500)


def get_setup_advice(session_id: int) -> str:
    laps = storage.get_laps(session_id)
    if not laps:
        return "No laps recorded yet. Complete a few laps first."

    lap_summaries = []
    for lap in laps[-10:]:
        tele = storage.get_telemetry(lap["id"])
        summary = _summarize_telemetry(tele) if tele else {}
        lap_summaries.append({**lap, **summary})

    prompt = _build_setup_prompt(lap_summaries)
    return _call_ai(_COACH_SYSTEM_PROMPT, [{"role": "user", "content": prompt}], max_tokens=1500)


# ---------------------------------------------------------------------------
# Interactive chat
# ---------------------------------------------------------------------------

def chat(conversation_history: list, user_message: str, session_id: int) -> str:
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

    messages = conversation_history + [
        {"role": "user", "content": user_message + context_block}
    ]
    return _call_ai(_COACH_SYSTEM_PROMPT, messages, max_tokens=800)


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
    if not tele:
        return {}
    speeds    = [t["speed_kmh"] for t in tele]
    throttles = [t["throttle"]  for t in tele]
    brakes    = [t["brake"]     for t in tele]
    steer     = [abs(t["steer_angle"]) for t in tele]
    tyre_fl   = [t["tyre_temp_fl"] for t in tele if t["tyre_temp_fl"]]
    tyre_fr   = [t["tyre_temp_fr"] for t in tele if t["tyre_temp_fr"]]
    tyre_rl   = [t["tyre_temp_rl"] for t in tele if t["tyre_temp_rl"]]
    tyre_rr   = [t["tyre_temp_rr"] for t in tele if t["tyre_temp_rr"]]
    brake_fl  = [t["brake_temp_fl"] for t in tele if t["brake_temp_fl"]]
    brake_fr  = [t["brake_temp_fr"] for t in tele if t["brake_temp_fr"]]

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
    car   = laps[0].get("car", "?") if laps else "?"
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
