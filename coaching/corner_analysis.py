"""
Corner-by-corner telemetry analysis.

Segments a lap's telemetry into corners using lateral G-force and
track position, then summarises each corner's entry/min/exit speed,
braking behaviour, and throttle application.
"""

from typing import List, Dict


def detect_corners(
    telemetry: List[Dict],
    lat_g_threshold: float = 0.3,
    min_samples: int = 4,
) -> List[Dict]:
    """
    Segment telemetry into corners by looking for sustained lateral G.

    Args:
        telemetry:        list of telemetry sample dicts from storage.get_telemetry()
        lat_g_threshold:  lateral G above this value = in a corner (default 0.3g)
        min_samples:      ignore micro-blips shorter than this many samples

    Returns:
        List of corner summary dicts, ordered by track position.
    """
    if not telemetry:
        return []

    corners: List[Dict] = []
    in_corner = False
    current: List[Dict] = []
    corner_num = 0

    for sample in telemetry:
        lat_g = abs(sample.get("g_lat", 0))
        if lat_g >= lat_g_threshold:
            if not in_corner:
                in_corner = True
                current = []
            current.append(sample)
        else:
            if in_corner:
                if len(current) >= min_samples:
                    corner_num += 1
                    corners.append(_summarize_corner(corner_num, current))
                in_corner = False
                current = []

    # Catch a corner that runs right to the end of the lap
    if in_corner and len(current) >= min_samples:
        corner_num += 1
        corners.append(_summarize_corner(corner_num, current))

    return corners


def _summarize_corner(num: int, samples: List[Dict]) -> Dict:
    speeds     = [s["speed_kmh"]            for s in samples]
    throttles  = [s["throttle"]             for s in samples]
    brakes     = [s["brake"]                for s in samples]
    lat_gs     = [abs(s.get("g_lat", 0))    for s in samples]

    entry_speed = speeds[0]
    min_speed   = min(speeds)
    exit_speed  = speeds[-1]

    track_pos   = samples[0].get("normalized_pos", 0) or 0
    duration_ms = samples[-1]["timestamp_ms"] - samples[0]["timestamp_ms"]

    avg_throttle = sum(throttles) / len(throttles)
    avg_brake    = sum(brakes)    / len(brakes)
    max_lat_g    = max(lat_gs)

    # Trail braking = still on brake in the first third of the corner
    first_third = samples[: max(1, len(samples) // 3)]
    trail_braking = any(
        s["brake"] > 0.1 and abs(s.get("g_lat", 0)) > lat_gs[0] * 0.6
        for s in first_third
    )

    # Early throttle = meaningful throttle before the corner midpoint
    midpoint = samples[: max(1, len(samples) // 2)]
    early_throttle = any(s["throttle"] > 0.25 for s in midpoint)

    # Speed loss through the corner
    speed_loss = entry_speed - min_speed

    return {
        "corner_number":   num,
        "track_position":  round(track_pos, 4),
        "entry_speed_kmh": round(entry_speed, 1),
        "min_speed_kmh":   round(min_speed,   1),
        "exit_speed_kmh":  round(exit_speed,  1),
        "speed_loss_kmh":  round(speed_loss,  1),
        "max_lat_g":       round(max_lat_g,   2),
        "avg_throttle":    round(avg_throttle, 3),
        "avg_brake":       round(avg_brake,    3),
        "duration_ms":     duration_ms,
        "trail_braking":   trail_braking,
        "early_throttle":  early_throttle,
    }


def format_corners_for_prompt(corners: List[Dict], car: str, track: str) -> str:
    """Format corner data into a string suitable for the AI coach prompt."""
    if not corners:
        return "No corner data available."

    lines = [f"Corner-by-corner breakdown — {car} at {track}:\n"]
    for c in corners:
        flags = []
        if c["trail_braking"]:
            flags.append("trail braking detected")
        if c["early_throttle"]:
            flags.append("early throttle detected")
        flags_str = f"  [{', '.join(flags)}]" if flags else ""

        lines.append(
            f"Corner {c['corner_number']} (track pos {c['track_position']:.1%}){flags_str}\n"
            f"  Speed: {c['entry_speed_kmh']} → {c['min_speed_kmh']} (min) → {c['exit_speed_kmh']} km/h"
            f"  |  Loss: {c['speed_loss_kmh']} km/h\n"
            f"  Max lat G: {c['max_lat_g']}g  |"
            f"  Avg throttle: {c['avg_throttle']:.0%}  |"
            f"  Avg brake: {c['avg_brake']:.0%}\n"
        )

    return "\n".join(lines)
