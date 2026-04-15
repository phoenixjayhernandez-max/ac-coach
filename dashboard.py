"""
AC Coach Dashboard — Streamlit web UI.

Run with:
    streamlit run dashboard.py

Then open http://localhost:8501 in your browser.
"""

import streamlit as st
import pandas as pd
import time

from database import storage
from coaching import ai_coach
from telemetry.reader import ms_to_laptime

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="AC Coach",
    page_icon="🏎️",
    layout="wide",
    initial_sidebar_state="expanded",
)

storage.init_db()

# ---------------------------------------------------------------------------
# Sidebar — session selector
# ---------------------------------------------------------------------------

st.sidebar.title("AC Coach")
st.sidebar.markdown("---")

sessions = storage.get_all_sessions()

if not sessions:
    st.title("AC Coach")
    st.info(
        "No sessions recorded yet.\n\n"
        "1. Make sure Assetto Corsa is running\n"
        "2. Open a second terminal and run: `python collector.py`\n"
        "3. Drive a few laps, then refresh this page"
    )
    st.stop()

session_labels = [
    f"{s['track']} — {s['car']}  ({s['lap_count']} laps)"
    for s in sessions
]
selected_idx = st.sidebar.selectbox(
    "Session", range(len(sessions)), format_func=lambda i: session_labels[i]
)
session = sessions[selected_idx]
session_id = session["id"]

st.sidebar.markdown(f"**Track:** {session['track']}")
st.sidebar.markdown(f"**Car:** {session['car']}")
st.sidebar.markdown(f"**Laps:** {session['lap_count']}")
if session.get("best_lap_ms"):
    st.sidebar.markdown(f"**Best lap:** {ms_to_laptime(session['best_lap_ms'])}")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_laps, tab_coach, tab_compare, tab_setup, tab_corners, tab_lb = st.tabs([
    "Lap History", "AI Coach", "Compare Laps", "Setup Advisor",
    "Corner Analysis", "Leaderboard",
])

laps = storage.get_laps(session_id)
df   = pd.DataFrame(laps) if laps else pd.DataFrame()

# ============================================================
# TAB 1 — LAP HISTORY
# ============================================================

with tab_laps:
    st.header("Lap History")

    if df.empty:
        st.info("No laps recorded for this session yet.")
    else:
        # Key metrics row
        best_ms = df[df["is_valid"] == 1]["lap_time_ms"].min() if not df.empty else 0
        avg_ms  = int(df[df["is_valid"] == 1]["lap_time_ms"].mean()) if not df.empty else 0
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Best Lap",    ms_to_laptime(best_ms))
        col2.metric("Average Lap", ms_to_laptime(avg_ms))
        col3.metric("Total Laps",  len(df))
        col4.metric("Track",       session["track"])

        st.markdown("---")

        # Lap time progression chart
        st.subheader("Lap Time Progression")
        chart_df = df[df["is_valid"] == 1][["lap_number", "lap_time_ms"]].copy()
        chart_df["lap_time_s"] = chart_df["lap_time_ms"] / 1000.0
        chart_df = chart_df.set_index("lap_number")
        st.line_chart(chart_df["lap_time_s"], use_container_width=True)

        # Lap table
        st.subheader("All Laps")
        display_df = df[["lap_number", "lap_time_ms", "max_speed_kmh",
                          "avg_throttle", "avg_brake", "tyre_compound",
                          "air_temp", "road_temp"]].copy()
        display_df["lap_time"] = display_df["lap_time_ms"].apply(ms_to_laptime)
        display_df["avg_throttle"] = (display_df["avg_throttle"] * 100).round(1).astype(str) + "%"
        display_df["avg_brake"]    = (display_df["avg_brake"]    * 100).round(1).astype(str) + "%"
        display_df = display_df.drop(columns=["lap_time_ms"])
        display_df = display_df.rename(columns={
            "lap_number":    "Lap",
            "lap_time":      "Time",
            "max_speed_kmh": "Max Speed (km/h)",
            "avg_throttle":  "Avg Throttle",
            "avg_brake":     "Avg Brake",
            "tyre_compound": "Tyres",
            "air_temp":      "Air °C",
            "road_temp":     "Road °C",
        })
        st.dataframe(display_df, use_container_width=True, hide_index=True)


# ============================================================
# TAB 2 — AI COACH (single lap analysis)
# ============================================================

with tab_coach:
    st.header("AI Coach")
    st.markdown("Select a lap to get personalized coaching feedback, or chat freely.")

    if df.empty:
        st.info("No laps recorded yet.")
    else:
        col_left, col_right = st.columns([1, 2])

        with col_left:
            st.subheader("Analyze a Lap")
            lap_options = {
                f"Lap {row['lap_number']}  —  {ms_to_laptime(row['lap_time_ms'])}": row["id"]
                for _, row in df.iterrows()
            }
            selected_lap_label = st.selectbox("Choose lap", list(lap_options.keys()))
            selected_lap_id = lap_options[selected_lap_label]

            if st.button("Get Coaching Feedback", type="primary"):
                with st.spinner("Analysing your lap..."):
                    feedback = ai_coach.analyze_lap(selected_lap_id, session_id)
                st.session_state["last_feedback"] = feedback

        with col_right:
            if "last_feedback" in st.session_state:
                st.subheader("Coach Feedback")
                st.markdown(st.session_state["last_feedback"])

        st.markdown("---")
        st.subheader("Ask Your Coach Anything")

        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []
            st.session_state["chat_messages"] = []   # display messages

        # Display chat history
        for msg in st.session_state["chat_messages"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        user_input = st.chat_input("Ask about your driving, setup, or anything racing related...")
        if user_input:
            # Show user message
            st.session_state["chat_messages"].append({"role": "user", "content": user_input})
            with st.chat_message("user"):
                st.markdown(user_input)

            # Get AI response
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    response = ai_coach.chat(
                        st.session_state["chat_history"],
                        user_input,
                        session_id,
                    )
                st.markdown(response)

            # Update histories
            st.session_state["chat_history"].append({"role": "user",      "content": user_input})
            st.session_state["chat_history"].append({"role": "assistant",  "content": response})
            st.session_state["chat_messages"].append({"role": "assistant", "content": response})


# ============================================================
# TAB 3 — COMPARE LAPS
# ============================================================

with tab_compare:
    st.header("Compare Two Laps")

    if df.empty or len(df) < 2:
        st.info("Record at least 2 laps to use comparison.")
    else:
        lap_options = {
            f"Lap {row['lap_number']}  —  {ms_to_laptime(row['lap_time_ms'])}": row["id"]
            for _, row in df.iterrows()
        }
        labels = list(lap_options.keys())

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Reference Lap (faster)**")
            ref_label = st.selectbox("Reference", labels, key="ref_lap")
            ref_id = lap_options[ref_label]
        with col2:
            st.markdown("**Your Lap (to improve)**")
            target_label = st.selectbox("Target", labels, index=min(1, len(labels)-1), key="tgt_lap")
            target_id = lap_options[target_label]

        if st.button("Compare Laps", type="primary"):
            if ref_id == target_id:
                st.warning("Please select two different laps.")
            else:
                with st.spinner("Comparing laps..."):
                    comparison = ai_coach.compare_laps(ref_id, target_id, session_id)
                st.markdown("---")
                st.markdown(comparison)


# ============================================================
# TAB 4 — SETUP ADVISOR
# ============================================================

with tab_setup:
    st.header("Setup Advisor")
    st.markdown(
        "Analyzes patterns across your recent laps and suggests specific car setup changes "
        "based on your driving data."
    )

    if df.empty:
        st.info("Record at least a few laps to get setup advice.")
    else:
        laps_available = min(len(df), 10)
        st.markdown(f"Will analyze your last **{laps_available} laps**.")

        if st.button("Get Setup Recommendations", type="primary"):
            with st.spinner("Analyzing your driving patterns..."):
                advice = ai_coach.get_setup_advice(session_id)
            st.markdown("---")
            st.markdown(advice)

        st.markdown("---")
        st.subheader("Raw Session Stats")
        if not df.empty:
            stats = {
                "Metric": [
                    "Best Lap", "Worst Lap", "Average Lap",
                    "Avg Max Speed (km/h)", "Avg Throttle %", "Avg Brake %"
                ],
                "Value": [
                    ms_to_laptime(df["lap_time_ms"].min()),
                    ms_to_laptime(df["lap_time_ms"].max()),
                    ms_to_laptime(int(df["lap_time_ms"].mean())),
                    f"{df['max_speed_kmh'].mean():.1f}",
                    f"{(df['avg_throttle'].mean() * 100):.1f}%",
                    f"{(df['avg_brake'].mean() * 100):.1f}%",
                ]
            }
            st.dataframe(pd.DataFrame(stats), use_container_width=True, hide_index=True)


# ============================================================
# TAB 5 — CORNER ANALYSIS
# ============================================================

with tab_corners:
    st.header("Corner Analysis")
    st.markdown(
        "Breaks your lap into individual corners using lateral G-force data, "
        "then asks the AI coach to identify exactly where you're losing time."
    )

    if df.empty:
        st.info("No laps recorded yet.")
    else:
        lap_options_c = {
            f"Lap {row['lap_number']}  —  {ms_to_laptime(row['lap_time_ms'])}": row["id"]
            for _, row in df.iterrows()
        }
        selected_c_label = st.selectbox("Choose lap", list(lap_options_c.keys()), key="corner_lap")
        selected_c_id    = lap_options_c[selected_c_label]

        if st.button("Analyze Corners", type="primary"):
            from coaching.corner_analysis import detect_corners
            tele    = storage.get_telemetry(selected_c_id)
            corners = detect_corners(tele)

            if not corners:
                st.warning(
                    "No corners detected. This lap's telemetry may not have normalized "
                    "position data — record a new lap and try again."
                )
            else:
                st.session_state["corners_raw"]      = corners
                st.session_state["corner_lap_id"]    = selected_c_id
                with st.spinner("Getting AI corner breakdown..."):
                    st.session_state["corner_feedback"] = ai_coach.analyze_corners(
                        selected_c_id, session_id
                    )

        if st.session_state.get("corners_raw"):
            col_cf1, col_cf2 = st.columns([2, 3])

            with col_cf1:
                st.subheader("Corner Data")
                corner_table = pd.DataFrame([{
                    "#":           c["corner_number"],
                    "Pos":         f"{c['track_position']:.1%}",
                    "Entry km/h":  c["entry_speed_kmh"],
                    "Min km/h":    c["min_speed_kmh"],
                    "Exit km/h":   c["exit_speed_kmh"],
                    "Max G":       c["max_lat_g"],
                    "Throttle":    f"{c['avg_throttle']:.0%}",
                    "Trail Brake": "✓" if c["trail_braking"]   else "",
                    "Early Thr":   "✓" if c["early_throttle"]  else "",
                } for c in st.session_state["corners_raw"]])
                st.dataframe(corner_table, use_container_width=True, hide_index=True)

            with col_cf2:
                st.subheader("AI Corner Feedback")
                st.markdown(st.session_state.get("corner_feedback", ""))


# ============================================================
# TAB 6 — LEADERBOARD / PERSONAL BESTS
# ============================================================

with tab_lb:
    st.header("Personal Bests & Progress")
    st.markdown("Your best lap times across every track and car combination, and how you've improved over time.")

    pbs = storage.get_personal_bests()

    if not pbs:
        st.info("No laps recorded yet across any session.")
    else:
        # Personal bests table
        st.subheader("Personal Bests")
        pb_df = pd.DataFrame([{
            "Track":       p["track"],
            "Car":         p["car"],
            "Best Lap":    ms_to_laptime(p["best_ms"]),
            "Sessions":    p["sessions"],
            "Total Laps":  p["total_laps"],
        } for p in pbs])
        st.dataframe(pb_df, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("Progress Over Time")

        combos          = [f"{p['track']} — {p['car']}" for p in pbs]
        selected_combo  = st.selectbox("Select track & car", combos, key="lb_combo")
        selected_pb     = pbs[combos.index(selected_combo)]

        progress = storage.get_progress(selected_pb["track"], selected_pb["car"])

        if progress:
            prog_df = pd.DataFrame(progress)
            prog_df["lap_time_s"] = prog_df["lap_time_ms"] / 1000.0
            prog_df["label"]      = (
                pd.to_datetime(prog_df["completed_at"], unit="s")
                .dt.strftime("%m/%d %H:%M")
            )
            prog_df = prog_df.sort_values("completed_at")

            st.line_chart(
                prog_df.set_index("label")["lap_time_s"],
                use_container_width=True,
            )

            # Show total improvement
            first_ms = progress[0]["lap_time_ms"]
            best_ms  = min(p["lap_time_ms"] for p in progress)
            gained   = first_ms - best_ms
            if gained > 0:
                st.success(
                    f"Total improvement: **−{ms_to_laptime(gained)}** "
                    f"from your first lap to your personal best."
                )
            elif len(progress) == 1:
                st.info("Only one lap recorded here — keep driving to see your progress chart.")
