"""
Alpine Analytics — Training Tracker

Coach-facing tool. Build a team roster, log training sessions end-of-day,
and track athlete development alongside their FIS race results.

How it works:
  - Add athletes by FIS code. The app pulls their race history from the DB.
  - Log each training run: venue, setter, homologation, athlete times / DNFs.
  - Peer delta (time vs team median on the same run) is computed automatically.
  - Athlete Detail tab shows race results + training peer delta side by side.

Training data is stored in this browser session.
Export CSV from the Log Session tab to save across sessions.
Persistent multi-coach storage is planned for a future release.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from database import query


# ─── Constants ────────────────────────────────────────────────────────────────

DISCIPLINES = ["Slalom", "Giant Slalom", "Super G", "Downhill", "Alpine Combined"]
ROLLING_MONTHS = 18


# ─── Session state init ───────────────────────────────────────────────────────

if "team_roster" not in st.session_state:
    # List of dicts: {fis_code, name, yob, country, discipline}
    st.session_state.team_roster = []

if "training_log" not in st.session_state:
    # Flat rows — one per athlete per run entry
    st.session_state.training_log = []


# ─── DB helpers ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def lookup_athlete(fis_code: str) -> dict | None:
    """Basic info for a single FIS code. Returns None if not found."""
    try:
        code = int(fis_code.strip())
    except ValueError:
        return None
    df = query(f"""
        SELECT DISTINCT ON (fr.fis_code)
            fr.fis_code::text AS fis_code,
            fr.name,
            fr.yob,
            fr.country
        FROM raw.fis_results fr
        WHERE fr.fis_code = {code}
        ORDER BY fr.fis_code, fr.name
        LIMIT 1
    """)
    return df.iloc[0].to_dict() if not df.empty else None


@st.cache_data(ttl=3600)
def load_athlete_races(fis_codes: tuple, months: int = ROLLING_MONTHS) -> pd.DataFrame:
    """Recent race results for a tuple of FIS codes."""
    if not fis_codes:
        return pd.DataFrame()
    code_list = ", ".join(str(int(c)) for c in fis_codes if str(c).isdigit())
    if not code_list:
        return pd.DataFrame()
    return query(f"""
        SELECT
            fr.fis_code::text  AS fis_code,
            fr.name,
            rd.date,
            rd.discipline,
            rd.race_type,
            rd.location,
            fr.rank,
            fr.fis_points
        FROM raw.fis_results fr
        JOIN raw.race_details rd ON rd.race_id = fr.race_id
        WHERE fr.fis_code IN ({code_list})
          AND rd.date >= CURRENT_DATE - INTERVAL '{months} months'
          AND fr.fis_points > 0
        ORDER BY rd.date DESC
    """)


# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Training Tracker — Alpine Analytics", layout="wide")


# ─── Sidebar: roster management ───────────────────────────────────────────────

st.sidebar.header("My Team")

with st.sidebar.expander("Add athlete", expanded=len(st.session_state.team_roster) == 0):
    code_input = st.text_input("FIS code", placeholder="e.g. 512182", key="add_fis_input")
    disc_input = st.selectbox("Primary discipline", DISCIPLINES, key="add_disc_input")
    if st.button("Add to roster", key="add_athlete_btn"):
        code_input = code_input.strip()
        if not code_input.isdigit():
            st.sidebar.error("Enter a numeric FIS code.")
        elif any(r["fis_code"] == code_input for r in st.session_state.team_roster):
            st.sidebar.warning("Already on the roster.")
        else:
            with st.spinner("Looking up athlete..."):
                info = lookup_athlete(code_input)
            if info is None:
                st.sidebar.error("No athlete found for that code.")
            else:
                st.session_state.team_roster.append({
                    "fis_code":   info["fis_code"],
                    "name":       info["name"],
                    "yob":        info.get("yob"),
                    "country":    info.get("country", ""),
                    "discipline": disc_input,
                })
                st.rerun()

if st.session_state.team_roster:
    st.sidebar.markdown("**Roster:**")
    for i, athlete in enumerate(st.session_state.team_roster):
        c1, c2 = st.sidebar.columns([5, 1])
        disc_abbr = {
            "Slalom": "SL", "Giant Slalom": "GS", "Super G": "SG",
            "Downhill": "DH", "Alpine Combined": "AC",
        }.get(athlete["discipline"], athlete["discipline"][:2])
        c1.markdown(f"{athlete['name']} · {disc_abbr}")
        if c2.button("✕", key=f"remove_{i}", help="Remove from roster"):
            st.session_state.team_roster.pop(i)
            st.rerun()
    st.sidebar.divider()
    if st.sidebar.button("Clear roster", type="secondary"):
        st.session_state.team_roster = []
        st.rerun()
else:
    st.sidebar.caption("No athletes added yet.")


# ─── Main page ────────────────────────────────────────────────────────────────

st.title("Training Tracker")
st.caption(
    "Add athletes by FIS code, log end-of-day training runs, and track "
    "development alongside race results. "
    "Training data lives in this browser session — export CSV to preserve it."
)

if not st.session_state.team_roster:
    st.info(
        "Add athletes to your roster using the sidebar. "
        "Enter any athlete's FIS code to pull their race history and start logging training."
    )
    st.stop()

tab_overview, tab_log, tab_athlete = st.tabs([
    "Team Overview", "Log Session", "Athlete Detail"
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — TEAM OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

with tab_overview:
    roster     = st.session_state.team_roster
    fis_codes  = tuple(r["fis_code"] for r in roster)

    with st.spinner("Loading race data..."):
        race_df = load_athlete_races(fis_codes)

    race_df["date"] = pd.to_datetime(race_df["date"]) if not race_df.empty else race_df

    # ── Summary table ─────────���───────────────────────────────────────────────
    st.subheader("Race Summary — Last 18 Months")

    rows = []
    for athlete in roster:
        fc         = athlete["fis_code"]
        a_races    = race_df[race_df["fis_code"] == fc] if not race_df.empty else pd.DataFrame()
        disc_races = a_races[a_races["discipline"] == athlete["discipline"]] if not a_races.empty else pd.DataFrame()
        last_row   = a_races.sort_values("date").iloc[-1] if not a_races.empty else None

        # FIS trend: slope over last 8 starts in primary discipline (pts/month)
        trend = None
        if len(disc_races) >= 3:
            recent = disc_races.sort_values("date").tail(8)
            days   = (recent["date"] - recent["date"].min()).dt.days.values.astype(float)
            pts    = recent["fis_points"].values
            if days.max() > 7:
                slope, _ = np.polyfit(days, pts, 1)
                trend = round(slope * 30, 2)  # pts/month; negative = improving

        training_sessions = len([
            r for r in st.session_state.training_log if r.get("fis_code") == fc
        ])

        rows.append({
            "Name":              athlete["name"],
            "Country":           athlete.get("country", ""),
            "YOB":               athlete.get("yob"),
            "Discipline":        athlete["discipline"],
            "Last Race":         last_row["date"].strftime("%Y-%m-%d") if last_row is not None else "—",
            "Last Venue":        last_row["location"]                  if last_row is not None else "—",
            "Last FIS":          round(last_row["fis_points"], 1)      if last_row is not None else None,
            "Best FIS (18mo)":   round(disc_races["fis_points"].min(), 1) if not disc_races.empty else None,
            "Races (18mo)":      len(disc_races),
            "Trend (pts/mo)":    trend,
            "Training Runs":     training_sessions,
        })

    overview_df = pd.DataFrame(rows)
    st.dataframe(
        overview_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Best FIS (18mo)":  st.column_config.NumberColumn(
                format="%.1f",
                help="Best single FIS result in primary discipline, last 18 months. Lower = faster.",
            ),
            "Last FIS":         st.column_config.NumberColumn(format="%.1f"),
            "Trend (pts/mo)":   st.column_config.NumberColumn(
                format="%+.2f",
                help="FIS points change per month (last 8 starts). Negative = improving.",
            ),
            "Training Runs":    st.column_config.NumberColumn(
                help="Total run entries logged in this session.",
            ),
            "YOB":              st.column_config.NumberColumn(format="%d"),
        },
    )

    # ── Team FIS progression chart ────────────────────────────────────────────
    if not race_df.empty:
        st.divider()
        st.subheader("FIS Points Over Time")
        chart_disc = st.selectbox("Discipline", DISCIPLINES, key="overview_disc")
        chart_data = race_df[race_df["discipline"] == chart_disc].sort_values("date")

        if chart_data.empty:
            st.info(f"No {chart_disc} race data for the current roster.")
        else:
            fig = px.line(
                chart_data,
                x="date", y="fis_points",
                color="name",
                markers=True,
                hover_data={"race_type": True, "location": True, "rank": True},
                labels={
                    "fis_points": "FIS Points (lower = faster)",
                    "date":       "Date",
                    "name":       "Athlete",
                },
                template="plotly_white",
            )
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(
                height=380,
                margin=dict(l=50, r=20, t=10, b=40),
                legend_title="Athlete",
            )
            st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — LOG SESSION
# ═══════════════════════════════════════════════════════════════════════════════

with tab_log:
    st.subheader("Log Training Session")
    st.caption(
        "Record one run at a time. Multiple runs in a day = submit once per run. "
        "Peer delta (time vs team median) is computed automatically."
    )

    roster = st.session_state.team_roster

    with st.form("training_session_form", clear_on_submit=True):
        # Session-level fields
        col1, col2, col3 = st.columns(3)
        with col1:
            session_date = st.date_input("Date", value=date.today())
            venue        = st.text_input("Venue", placeholder="e.g. Soelden")
        with col2:
            discipline   = st.selectbox("Discipline", DISCIPLINES, key="log_disc")
            run_number   = st.number_input("Run #", min_value=1, max_value=4, value=1, step=1)
        with col3:
            setter       = st.text_input("Course setter", placeholder="Full name")
            homologation = st.text_input(
                "Homologation no. (optional)", placeholder="e.g. 123/FIS/01",
                help="Links to course trait database for future course-fit analysis.",
            )

        st.markdown("---")
        st.markdown("**Athlete times**")
        st.caption(
            "Time in seconds (e.g. 58.43). Leave blank if DNF or DSQ — "
            "the status dropdown controls what is saved."
        )

        # Per-athlete entry — 3 across
        athlete_entries = []
        COLS_PER_ROW    = 3
        for row_start in range(0, len(roster), COLS_PER_ROW):
            row_slice = roster[row_start : row_start + COLS_PER_ROW]
            cols      = st.columns(COLS_PER_ROW)
            for col_idx, athlete in enumerate(row_slice):
                with cols[col_idx]:
                    st.markdown(f"**{athlete['name']}**")
                    bib    = st.number_input(
                        "Bib", min_value=1, max_value=200,
                        value=row_start + col_idx + 1,
                        key=f"bib_{athlete['fis_code']}",
                        step=1,
                    )
                    t_str  = st.text_input(
                        "Time (s)", placeholder="e.g. 58.43",
                        key=f"time_{athlete['fis_code']}",
                    )
                    status = st.selectbox(
                        "Status", ["Finish", "DNF", "DSQ"],
                        key=f"status_{athlete['fis_code']}",
                    )
                    athlete_entries.append((athlete, bib, t_str, status))

        submitted = st.form_submit_button("Save run", type="primary")

    if submitted:
        saved  = 0
        errors = []
        for athlete, bib, t_str, status in athlete_entries:
            time_sec = None
            if status == "Finish":
                if not t_str.strip():
                    errors.append(f"{athlete['name']}: time is required for Finish.")
                    continue
                try:
                    time_sec = float(t_str.strip())
                except ValueError:
                    errors.append(f"{athlete['name']}: invalid time '{t_str}'.")
                    continue
            st.session_state.training_log.append({
                "date":         str(session_date),
                "venue":        venue.strip(),
                "homologation": homologation.strip(),
                "discipline":   discipline,
                "run_number":   int(run_number),
                "setter":       setter.strip(),
                "fis_code":     athlete["fis_code"],
                "name":         athlete["name"],
                "country":      athlete.get("country", ""),
                "bib":          int(bib),
                "time_seconds": time_sec,
                "status":       status,
            })
            saved += 1

        for e in errors:
            st.error(e)
        if saved:
            st.success(f"Saved {saved} result(s) — {session_date}, {venue or 'venue not set'}, run {int(run_number)}.")

    # ── Training log ──────────────────────────────────────────────────────────
    if st.session_state.training_log:
        st.divider()
        st.subheader("Training Log — This Session")

        log_df = pd.DataFrame(st.session_state.training_log)

        # Compute peer delta: time vs median for same date / venue / discipline / run
        grp_keys = ["date", "venue", "discipline", "run_number"]
        medians  = (
            log_df[log_df["time_seconds"].notna()]
            .groupby(grp_keys)["time_seconds"]
            .median()
            .rename("median_time")
        )
        log_df = log_df.join(medians, on=grp_keys)
        log_df["peer_delta"] = (log_df["time_seconds"] - log_df["median_time"]).round(3)
        log_df["vs Peers"]   = log_df["peer_delta"].apply(
            lambda x: f"{x:+.2f}s" if pd.notna(x) else "—"
        )

        show = ["date", "venue", "discipline", "run_number", "setter",
                "name", "bib", "time_seconds", "status", "vs Peers"]
        display_log = (
            log_df[show]
            .sort_values(["date", "venue", "run_number", "bib"])
            .rename(columns={
                "date": "Date", "venue": "Venue", "discipline": "Discipline",
                "run_number": "Run", "setter": "Setter",
                "name": "Name", "bib": "Bib",
                "time_seconds": "Time (s)", "status": "Status",
            })
        )
        st.dataframe(display_log, use_container_width=True, hide_index=True)

        col_dl, col_clr = st.columns([2, 1])
        csv_export = log_df.drop(columns=["median_time", "peer_delta", "vs Peers"], errors="ignore").to_csv(index=False)
        col_dl.download_button(
            "Download log as CSV", data=csv_export,
            file_name="training_log.csv", mime="text/csv",
        )
        if col_clr.button("Clear log", type="secondary"):
            st.session_state.training_log = []
            st.rerun()
    else:
        st.info("No sessions logged yet. Fill in the form above and click Save run.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — ATHLETE DETAIL
# ═══════════════════════════════════════════════════════════════════════════════

with tab_athlete:
    roster   = st.session_state.team_roster
    sel_name = st.selectbox(
        "Athlete", [r["name"] for r in roster], key="detail_sel"
    )
    sel      = next(r for r in roster if r["name"] == sel_name)
    fc       = sel["fis_code"]
    prim     = sel["discipline"]

    with st.spinner("Loading..."):
        race_df2 = load_athlete_races((fc,))

    if not race_df2.empty:
        race_df2["date"] = pd.to_datetime(race_df2["date"])

    disc_races = (
        race_df2[race_df2["discipline"] == prim].sort_values("date")
        if not race_df2.empty else pd.DataFrame()
    )

    athlete_log = pd.DataFrame([
        r for r in st.session_state.training_log if r.get("fis_code") == fc
    ]) if st.session_state.training_log else pd.DataFrame()

    # ── Header metrics ────────────────────────────────────────────────────────
    age = (2026 - int(sel["yob"])) if sel.get("yob") else None
    st.markdown(
        f"### {sel_name}"
        f"{'  ·  ' + sel.get('country','') if sel.get('country') else ''}"
        f"{'  ·  Age ' + str(age) if age else ''}"
        f"{'  ·  Born ' + str(int(sel['yob'])) if sel.get('yob') else ''}"
        f"  ·  {prim}"
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("FIS Code", fc)
    m2.metric(
        f"Best FIS — {prim[:2]}",
        f"{disc_races['fis_points'].min():.1f}" if not disc_races.empty else "—",
        help="Best single result in primary discipline, last 18 months.",
    )
    m3.metric("Races (18mo)", len(disc_races))
    m4.metric(
        "All disciplines (18mo)",
        len(race_df2) if not race_df2.empty else 0,
    )
    m5.metric("Training runs logged", len(athlete_log) if not athlete_log.empty else 0)

    st.divider()

    # ── Race results ──────────────────────────────────────────────────────────
    col_race, col_all = st.columns([3, 1])

    with col_race:
        st.subheader(f"Race Results — {prim}")
        if disc_races.empty:
            st.info(f"No {prim} results in the last {ROLLING_MONTHS} months.")
        else:
            fig_race = go.Figure()
            fig_race.add_trace(go.Scatter(
                x=disc_races["date"],
                y=disc_races["fis_points"],
                mode="lines+markers",
                name="FIS Points",
                line=dict(color="#1a3a6b", width=2),
                marker=dict(size=7),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "FIS: %{y:.1f}<br>"
                    "Rank: %{customdata[1]}<br>"
                    "Type: %{customdata[2]}<extra></extra>"
                ),
                customdata=disc_races[["location", "rank", "race_type"]].values,
            ))
            if len(disc_races) >= 3:
                roll = disc_races["fis_points"].rolling(5, min_periods=2).mean()
                fig_race.add_trace(go.Scatter(
                    x=disc_races["date"], y=roll,
                    mode="lines", name="5-race avg",
                    line=dict(color="#cc0000", dash="dash", width=1.5),
                ))
            fig_race.update_yaxes(autorange="reversed", title="FIS Points (lower = faster)")
            fig_race.update_xaxes(title="Date")
            fig_race.update_layout(
                height=320, template="plotly_white",
                margin=dict(l=50, r=20, t=10, b=40), legend_title="",
            )
            st.plotly_chart(fig_race, use_container_width=True)

    with col_all:
        st.subheader("By Discipline")
        if not race_df2.empty:
            disc_summary = (
                race_df2.groupby("discipline")
                .agg(Races=("fis_points", "count"), Best_FIS=("fis_points", "min"))
                .reset_index()
                .rename(columns={"discipline": "Discipline", "Best_FIS": "Best FIS"})
                .sort_values("Best FIS")
            )
            disc_summary["Best FIS"] = disc_summary["Best FIS"].round(1)
            st.dataframe(disc_summary, use_container_width=True, hide_index=True)
        else:
            st.info("No data.")

    # ── Recent race table ─────────────────────────────────────────────────────
    if not disc_races.empty:
        with st.expander("Recent race list", expanded=False):
            show_races = (
                disc_races.sort_values("date", ascending=False)
                .head(20)[["date", "location", "race_type", "rank", "fis_points"]]
                .rename(columns={
                    "date": "Date", "location": "Venue", "race_type": "Type",
                    "rank": "Rank", "fis_points": "FIS Points",
                })
            )
            st.dataframe(show_races, use_container_width=True, hide_index=True)

    st.divider()

    # ── Training: peer delta chart ─────────────────────────────────────────────
    st.subheader("Training — Peer Delta")
    st.caption(
        "Time gap vs team median on the same run. "
        "Negative (chart top) = faster than peers that day."
    )

    if athlete_log.empty:
        st.info("No training runs logged for this athlete yet. Use the Log Session tab.")
    else:
        athlete_log["date_dt"] = pd.to_datetime(athlete_log["date"])
        grp_keys = ["date", "venue", "discipline", "run_number"]

        all_log  = pd.DataFrame(st.session_state.training_log)
        medians3 = (
            all_log[all_log["time_seconds"].notna()]
            .groupby(grp_keys)["time_seconds"]
            .median()
            .rename("median_time")
        )
        athlete_log = athlete_log.join(medians3, on=grp_keys)
        athlete_log["peer_delta"] = athlete_log["time_seconds"] - athlete_log["median_time"]

        finished = athlete_log[athlete_log["status"] == "Finish"].sort_values("date_dt")

        if finished.empty:
            st.info("No finished training runs to plot.")
        else:
            fig_tr = px.scatter(
                finished,
                x="date_dt", y="peer_delta",
                color="discipline",
                symbol="venue",
                hover_data={
                    "venue": True, "run_number": True,
                    "time_seconds": ":.2f", "setter": True,
                },
                labels={
                    "date_dt":    "Date",
                    "peer_delta": "Time vs Peers (s — negative = faster)",
                    "discipline": "Discipline",
                    "venue":      "Venue",
                },
                template="plotly_white",
            )
            fig_tr.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.5)
            fig_tr.update_yaxes(autorange="reversed")
            fig_tr.update_layout(
                height=300,
                margin=dict(l=50, r=20, t=10, b=40),
            )
            st.plotly_chart(fig_tr, use_container_width=True)

        # Training log table for this athlete
        show_log = ["date", "venue", "discipline", "run_number", "setter",
                    "bib", "time_seconds", "status"]
        tbl = (
            athlete_log[[c for c in show_log if c in athlete_log.columns]]
            .sort_values(["date", "run_number", "bib"])
            .rename(columns={
                "date": "Date", "venue": "Venue", "discipline": "Discipline",
                "run_number": "Run", "setter": "Setter",
                "bib": "Bib", "time_seconds": "Time (s)", "status": "Status",
            })
        )
        st.dataframe(tbl, use_container_width=True, hide_index=True)
