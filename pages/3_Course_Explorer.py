"""
Alpine Analytics — Course Explorer
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from database import query



# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400)
def load_venue_list() -> pd.DataFrame:
    return query("""
        SELECT location, discipline, COUNT(*) AS races
        FROM raw.race_details
        WHERE location IS NOT NULL AND discipline IS NOT NULL
        GROUP BY location, discipline
        ORDER BY location, discipline
    """)


@st.cache_data(ttl=86400)
def load_venue_profile(location: str, discipline: str) -> pd.DataFrame:
    df = query("""
        SELECT
            rd.date,
            rd.race_type,
            rd.sex,
            rd.homologation_number,
            rd.vertical_drop,
            rd.start_altitude,
            rd.finish_altitude,
            rd.first_run_number_of_gates          AS gates_r1,
            rd.first_run_turning_gates             AS turning_r1,
            rd.first_run_course_setter             AS setter_r1,
            rd.second_run_number_of_gates          AS gates_r2,
            rd.second_run_course_setter            AS setter_r2,
            COALESCE(sg.field_size, 0)             AS field_size,
            sg.avg_fis,
            sg.best_fis,
            sg.field_spread,
            w.winner,
            w.winner_z
        FROM raw.race_details rd
        LEFT JOIN (
            SELECT race_id,
                   COUNT(*)           AS field_size,
                   AVG(fis_points)    AS avg_fis,
                   MIN(fis_points)    AS best_fis,
                   PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY fis_points)
                   - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY fis_points) AS field_spread
            FROM race_aggregate.strokes_gained
            GROUP BY race_id
        ) sg ON sg.race_id = rd.race_id
        LEFT JOIN (
            SELECT DISTINCT ON (race_id)
                   race_id,
                   name          AS winner,
                   race_z_score  AS winner_z
            FROM race_aggregate.strokes_gained
            ORDER BY race_id, fis_points ASC
        ) w ON w.race_id = rd.race_id
        WHERE rd.location   = :location
          AND rd.discipline = :discipline
        ORDER BY rd.date ASC
    """, {"location": location, "discipline": discipline})
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=86400)
def load_discipline_averages(discipline: str) -> dict:
    df = query("""
        SELECT
            AVG(vertical_drop)                  AS avg_vert,
            AVG(first_run_number_of_gates)       AS avg_gates_r1,
            AVG(first_run_turning_gates)         AS avg_turning_r1,
            AVG(start_altitude)                  AS avg_start_alt,
            AVG(CASE WHEN first_run_number_of_gates > 0 AND first_run_turning_gates > 0
                     THEN first_run_turning_gates::numeric / first_run_number_of_gates * 100
                     ELSE NULL END)              AS avg_turning_pct
        FROM raw.race_details
        WHERE discipline = :discipline
          AND first_run_number_of_gates IS NOT NULL
    """, {"discipline": discipline})
    if not df.empty:
        return df.iloc[0].to_dict()
    return {}


@st.cache_data(ttl=86400)
def load_setter_leaderboard(discipline: str | None = None) -> pd.DataFrame:
    params: dict = {}
    disc_filter = ""
    if discipline:
        disc_filter = "AND rd.discipline = :discipline"
        params["discipline"] = discipline
    return query(f"""
        SELECT
            rd.first_run_course_setter                                  AS setter,
            rd.first_run_course_setter_country                          AS country,
            COUNT(DISTINCT rd.race_id)                                  AS races,
            ROUND(AVG(rd.first_run_number_of_gates)::numeric, 1)       AS avg_gates,
            ROUND(AVG(rd.vertical_drop)::numeric, 0)                   AS avg_vert,
            ROUND(AVG(iqr.iqr_fis)::numeric, 1)                        AS avg_field_spread,
            ROUND(AVG(wz.winner_z)::numeric, 3)                        AS avg_winner_z
        FROM raw.race_details rd
        JOIN race_aggregate.strokes_gained sg ON sg.race_id = rd.race_id
        LEFT JOIN (
            SELECT race_id,
                   PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY fis_points)
                   - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY fis_points) AS iqr_fis
            FROM race_aggregate.strokes_gained GROUP BY race_id
        ) iqr ON iqr.race_id = rd.race_id
        LEFT JOIN (
            SELECT race_id, MAX(race_z_score) AS winner_z
            FROM race_aggregate.strokes_gained GROUP BY race_id
        ) wz ON wz.race_id = rd.race_id
        WHERE rd.first_run_course_setter IS NOT NULL
          {disc_filter}
        GROUP BY rd.first_run_course_setter, rd.first_run_course_setter_country
        HAVING COUNT(DISTINCT rd.race_id) >= 3
        ORDER BY races DESC
    """, params or None)


@st.cache_data(ttl=86400)
def load_setter_races(setter: str) -> pd.DataFrame:
    df = query("""
        SELECT
            rd.date, rd.location, rd.discipline, rd.race_type, rd.sex,
            rd.vertical_drop,
            rd.first_run_number_of_gates AS gates_r1,
            sg.field_size,
            ROUND(sg.avg_fis::numeric, 1) AS avg_fis,
            w.winner
        FROM raw.race_details rd
        LEFT JOIN (
            SELECT race_id, COUNT(*) AS field_size, AVG(fis_points) AS avg_fis
            FROM race_aggregate.strokes_gained GROUP BY race_id
        ) sg ON sg.race_id = rd.race_id
        LEFT JOIN (
            SELECT DISTINCT ON (race_id) race_id, name AS winner
            FROM race_aggregate.strokes_gained ORDER BY race_id, fis_points ASC
        ) w ON w.race_id = rd.race_id
        WHERE rd.first_run_course_setter = :setter
        ORDER BY rd.date DESC
    """, {"setter": setter})
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=86400)
def load_best_courses(discipline: str | None = None, min_performances: int = 3) -> pd.DataFrame:
    params: dict = {"min_perf": min_performances}
    disc_filter = ""
    if discipline:
        disc_filter = "AND bc.discipline = :discipline"
        params["discipline"] = discipline
    return query(f"""
        SELECT
            bc.location, bc.discipline, bc.homologation_number,
            bc.mean_z_score, bc.performance_count, bc.rank,
            rd.country,
            ROUND(AVG(rd.vertical_drop)::numeric, 0)             AS avg_vert,
            ROUND(AVG(rd.first_run_number_of_gates)::numeric, 1) AS avg_gates
        FROM course_aggregate.best_courses bc
        LEFT JOIN raw.race_details rd
          ON rd.location = bc.location
         AND rd.discipline = bc.discipline
         AND rd.homologation_number = bc.homologation_number
        WHERE bc.performance_count >= :min_perf
          {disc_filter}
        GROUP BY bc.location, bc.discipline, bc.homologation_number,
                 bc.mean_z_score, bc.performance_count, bc.rank, rd.country
        ORDER BY bc.mean_z_score DESC
    """, params)


@st.cache_data(ttl=86400)
def load_hdi(discipline: str | None = None, min_races: int = 2) -> pd.DataFrame:
    params: dict = {"min_races": min_races}
    disc_filter = ""
    if discipline:
        disc_filter = "AND discipline = :discipline"
        params["discipline"] = discipline
    return query(f"""
        SELECT
            location, country, discipline, homologation_number,
            race_count, avg_winning_time,
            avg_gate_count, avg_vertical_drop, avg_start_altitude,
            ROUND(avg_dnf_rate::numeric * 100, 1) AS dnf_pct,
            hill_difficulty_index                  AS hdi
        FROM course_aggregate.difficulty_index
        WHERE hill_difficulty_index IS NOT NULL
          AND race_count >= :min_races
          {disc_filter}
        ORDER BY hill_difficulty_index DESC
    """, params)


@st.cache_data(ttl=86400)
def load_hdi_detail(location: str, discipline: str, homologation: str) -> dict:
    """Full HDI row including normalized component scores for a specific course."""
    df = query("""
        SELECT
            location, discipline, homologation_number,
            race_count, avg_winning_time, avg_winning_time AS win_time_str,
            avg_gate_count, avg_vertical_drop, avg_start_altitude,
            ROUND(avg_dnf_rate::numeric * 100, 1)   AS dnf_pct,
            hill_difficulty_index                    AS hdi,
            COALESCE(winning_time_norm, 0)           AS winning_time_norm,
            COALESCE(gate_count_norm, 0)             AS gate_count_norm,
            COALESCE(start_altitude_norm, 0)         AS start_altitude_norm,
            COALESCE(vertical_drop_norm, 0)          AS vertical_drop_norm,
            COALESCE(dnf_rate_norm, 0)               AS dnf_rate_norm
        FROM course_aggregate.difficulty_index
        WHERE location            = :location
          AND discipline          = :discipline
          AND homologation_number = :homo
        LIMIT 1
    """, {"location": location, "discipline": discipline, "homo": homologation})
    if not df.empty:
        return df.iloc[0].to_dict()
    return {}


@st.cache_data(ttl=86400)
def load_similar_courses(location: str, discipline: str, homologation: str) -> pd.DataFrame:
    """Top similar courses for a given reference course."""
    df = query("""
        SELECT
            sc.similar_location                         AS location,
            sc.similar_homologation                     AS homologation_number,
            ROUND(sc.similarity_score::numeric, 3)      AS similarity_score,
            sc.similar_winning_time                     AS avg_winning_time_min,
            ROUND(sc.similar_gate_count::numeric, 1)    AS avg_gates,
            ROUND(sc.similar_start_altitude::numeric, 0) AS avg_start_alt,
            ROUND(sc.similar_vertical_drop::numeric, 0) AS avg_vert,
            ROUND(sc.similar_dnf_rate::numeric * 100, 1) AS dnf_pct,
            ROUND(sc.similar_fis_points::numeric, 1)    AS avg_fis,
            rd.country
        FROM course_aggregate.similar_courses sc
        LEFT JOIN (
            SELECT DISTINCT location, country
            FROM raw.race_details
            WHERE country IS NOT NULL
        ) rd ON rd.location = sc.similar_location
        WHERE sc.ref_location      = :location
          AND sc.ref_discipline    = :discipline
          AND sc.ref_homologation  = :homo
        ORDER BY sc.similarity_score ASC
        LIMIT 10
    """, {"location": location, "discipline": discipline, "homo": homologation})
    return df


@st.cache_data(ttl=86400)
def load_course_search(
    disciplines: tuple,
    min_vert: int | None,
    max_vert: int | None,
    min_gates: int | None,
    max_gates: int | None,
    race_types: tuple,
    date_from,
    date_to,
) -> pd.DataFrame:
    filters: list = []
    params: dict = {}
    if disciplines:
        disc_str = ", ".join(f":disc_{i}" for i in range(len(disciplines)))
        for i, d in enumerate(disciplines):
            params[f"disc_{i}"] = d
        filters.append(f"rd.discipline IN ({disc_str})")
    if min_vert:
        filters.append("rd.vertical_drop >= :min_vert")
        params["min_vert"] = min_vert
    if max_vert:
        filters.append("rd.vertical_drop <= :max_vert")
        params["max_vert"] = max_vert
    if min_gates:
        filters.append("rd.first_run_number_of_gates >= :min_gates")
        params["min_gates"] = min_gates
    if max_gates:
        filters.append("rd.first_run_number_of_gates <= :max_gates")
        params["max_gates"] = max_gates
    if race_types:
        rt_str = ", ".join(f":rt_{i}" for i in range(len(race_types)))
        for i, rt in enumerate(race_types):
            params[f"rt_{i}"] = rt
        filters.append(f"rd.race_type IN ({rt_str})")
    if date_from:
        filters.append("rd.date >= :date_from")
        params["date_from"] = str(date_from)
    if date_to:
        filters.append("rd.date <= :date_to")
        params["date_to"] = str(date_to)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    df = query(f"""
        SELECT
            rd.date, rd.location, rd.discipline, rd.race_type, rd.sex, rd.country,
            rd.vertical_drop, rd.start_altitude,
            rd.first_run_number_of_gates  AS gates_r1,
            rd.first_run_turning_gates    AS turning_r1,
            rd.first_run_course_setter    AS setter_r1,
            sg.field_size,
            ROUND(sg.avg_fis::numeric, 1) AS avg_fis
        FROM raw.race_details rd
        LEFT JOIN (
            SELECT race_id, COUNT(*) AS field_size, AVG(fis_points) AS avg_fis
            FROM race_aggregate.strokes_gained GROUP BY race_id
        ) sg ON sg.race_id = rd.race_id
        {where}
        ORDER BY rd.date DESC LIMIT 500
    """, params or None)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

st.sidebar.title("Course Explorer")

page = st.sidebar.radio(
    "Section",
    ["Venue Profile", "Best Courses", "Hill Difficulty (HDI)", "Course Similarity", "Venue Comparison", "Course Search", "Course Setters"],
    key="course_nav",
)
st.sidebar.markdown("---")

# Load venue list once (used by multiple sections)
try:
    venue_list = load_venue_list()
except Exception as e:
    st.error(f"Could not connect to database: {e}")
    st.stop()

st.title("Course Explorer")


# ===========================================================================
# VENUE PROFILE
# ===========================================================================

if page == "Venue Profile":
    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Venue Profile** gives a complete picture of a specific race location — every edition held
            there in the database, how the course has evolved, and how competitive the field has been over time.

            - **Course Profile vs Discipline Average** — compares this venue's gate count, vertical drop,
              start altitude, and turning gate % to the average for all races in the same discipline.
              Use this to understand whether the venue sets tighter, more open, or higher-altitude courses
              than typical.
            - **Gate Count Over Time** — tracks how the number of gates has changed across editions.
              Course setters vary their designs year to year; this shows those trends.
            - **Field Quality Over Time** — FIS points of the field and the winner per edition.
              Lower FIS = faster, stronger athlete. A declining trend means the event is attracting
              better competition over time.
            - **Field Spread Over Time** — the IQR (interquartile range) of FIS points each edition.
              A wider spread means a more decisive race where top skiers separated from the field.
              A tight spread means a closely contested race.
            - **Homologation numbers** identify the specific course configuration on the mountain.
              A single hill can have multiple configurations (different start gates, course lines),
              each registered with a unique homologation number. Use the sidebar filter to focus on one.
            """
        )
    if venue_list.empty:
        st.warning("No venue data found.")
    else:
        # Sidebar selectors
        all_disc_v = sorted(venue_list["discipline"].dropna().unique().tolist())
        sel_disc_v = st.sidebar.selectbox("Discipline", all_disc_v, key="venue_disc")

        venues_for_disc = sorted(
            venue_list[venue_list["discipline"] == sel_disc_v]["location"]
            .dropna().unique().tolist()
        )
        location_search = st.sidebar.text_input("Search venue", placeholder="e.g. Adelboden", key="venue_search")
        if location_search.strip():
            venues_for_disc = [v for v in venues_for_disc if location_search.strip().lower() in v.lower()]

        sel_venue = st.sidebar.selectbox("Venue", venues_for_disc, key="venue_loc")

        if sel_venue:
            with st.spinner("Loading venue data..."):
                vp = load_venue_profile(sel_venue, sel_disc_v)
                disc_avgs = load_discipline_averages(sel_disc_v)

            if vp.empty:
                st.info("No race data found for this venue and discipline.")
            else:
                # Homologation filter
                all_homos = sorted(vp["homologation_number"].dropna().unique().tolist())
                if len(all_homos) > 1:
                    sel_homos = st.sidebar.multiselect(
                        "Homologation number",
                        all_homos,
                        default=all_homos,
                        key="venue_homo",
                        help="Hills can have multiple homologation numbers for different course areas on the same mountain.",
                    )
                    if sel_homos:
                        vp = vp[vp["homologation_number"].isin(sel_homos)]
                    homo_label = ", ".join(sel_homos) if sel_homos else "—"
                else:
                    homo_label = all_homos[0] if all_homos else "—"

                if vp.empty:
                    st.info("No data for the selected homologation numbers.")
                    st.stop()

                st.subheader(f"{sel_venue}  ·  {sel_disc_v}")

                # Top metrics
                s1, s2, s3, s4, s5 = st.columns(5)
                s1.metric("Total Editions", len(vp))

                vert = vp["vertical_drop"].dropna()
                if not vert.empty:
                    s2.metric("Vertical Drop", f"{int(vert.mean())} m")

                alt_s = vp["start_altitude"].dropna()
                alt_f = vp["finish_altitude"].dropna()
                if not alt_s.empty and not alt_f.empty:
                    s3.metric("Altitude", f"{int(alt_s.mean())} → {int(alt_f.mean())} m")

                gates = vp["gates_r1"].dropna()
                if not gates.empty:
                    disc_gate_avg = disc_avgs.get("avg_gates_r1")
                    delta = (
                        f"{gates.mean() - disc_gate_avg:+.0f} vs discipline avg"
                        if disc_gate_avg else None
                    )
                    s4.metric("Avg Gates (R1)", f"{gates.mean():.0f}", delta=delta)

                if vp["avg_fis"].notna().any():
                    s5.metric("Avg Field FIS", f"{vp['avg_fis'].mean():.1f}")

                st.caption(
                    f"Homologation: **{homo_label}**  ·  "
                    f"*(Multiple homologation numbers = different course areas on the same mountain)*"
                    if len(all_homos) > 1 else
                    f"Homologation: **{homo_label}**"
                )

                st.markdown("---")

                # Course characteristics vs discipline average
                st.subheader("Course Profile vs Discipline Average")
                traits = []
                if not vp["gates_r1"].dropna().empty and disc_avgs.get("avg_gates_r1"):
                    traits.append(("Avg Gates R1", vp["gates_r1"].mean(), disc_avgs["avg_gates_r1"]))
                if not vp["vertical_drop"].dropna().empty and disc_avgs.get("avg_vert"):
                    traits.append(("Vertical Drop (m)", vp["vertical_drop"].mean(), disc_avgs["avg_vert"]))
                if not vp["start_altitude"].dropna().empty and disc_avgs.get("avg_start_alt"):
                    traits.append(("Start Altitude (m)", vp["start_altitude"].mean(), disc_avgs["avg_start_alt"]))
                tp_vals = [
                    row["turning_r1"] / row["gates_r1"] * 100
                    for _, row in vp.iterrows()
                    if row.get("gates_r1") and row.get("turning_r1") and row["gates_r1"] > 0
                ]
                if tp_vals and disc_avgs.get("avg_turning_pct"):
                    traits.append(("Turning Gate %", sum(tp_vals) / len(tp_vals), disc_avgs["avg_turning_pct"]))

                if traits:
                    trait_df = pd.DataFrame(traits, columns=["Trait", "This Venue", "Discipline Avg"])
                    fig_trait = go.Figure()
                    fig_trait.add_trace(go.Bar(
                        name="This Venue", x=trait_df["Trait"], y=trait_df["This Venue"],
                        marker_color="#1f77b4",
                        text=trait_df["This Venue"].round(1), textposition="outside",
                    ))
                    fig_trait.add_trace(go.Bar(
                        name="Discipline Avg", x=trait_df["Trait"], y=trait_df["Discipline Avg"],
                        marker_color="#aaaaaa",
                        text=trait_df["Discipline Avg"].round(1), textposition="outside",
                    ))
                    fig_trait.update_layout(
                        barmode="group", height=340, margin=dict(t=20, b=20),
                        legend=dict(orientation="h", y=1.08),
                    )
                    st.plotly_chart(fig_trait, use_container_width=True)

                st.markdown("---")

                # Gate count trend
                gates_over_time = vp.dropna(subset=["gates_r1"])
                if len(gates_over_time) >= 2:
                    st.subheader("Gate Count Over Time")
                    fig_gates = go.Figure()
                    fig_gates.add_trace(go.Scatter(
                        x=gates_over_time["date"], y=gates_over_time["gates_r1"],
                        mode="lines+markers", name="Gates R1",
                        line=dict(color="#1f77b4", width=2),
                        text=gates_over_time["setter_r1"],
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Gates R1: %{y}<br>Setter: %{text}<extra></extra>",
                    ))
                    if gates_over_time["gates_r2"].notna().any():
                        fig_gates.add_trace(go.Scatter(
                            x=gates_over_time["date"], y=gates_over_time["gates_r2"],
                            mode="lines+markers", name="Gates R2",
                            line=dict(color="#ff7f0e", width=2, dash="dash"),
                            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Gates R2: %{y}<extra></extra>",
                        ))
                    if disc_avgs.get("avg_gates_r1"):
                        fig_gates.add_hline(
                            y=disc_avgs["avg_gates_r1"], line_dash="dot", line_color="gray",
                            annotation_text="Discipline avg", annotation_position="right",
                        )
                    fig_gates.update_layout(
                        height=300, margin=dict(t=20, b=20),
                        yaxis_title="Gate Count", xaxis_title="Edition",
                        legend=dict(orientation="h", y=1.08),
                    )
                    st.plotly_chart(fig_gates, use_container_width=True)

                st.markdown("---")

                # Field quality trend
                if vp["avg_fis"].notna().any():
                    st.subheader("Field Quality Over Time")
                    fig_fq = go.Figure()
                    fig_fq.add_trace(go.Scatter(
                        x=vp["date"], y=vp["avg_fis"],
                        mode="lines+markers", name="Field Avg FIS",
                        line=dict(color="#aec7e8", width=2),
                        text=vp["setter_r1"],
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Field Avg FIS: %{y:.1f}<br>Setter: %{text}<extra></extra>",
                    ))
                    if vp["best_fis"].notna().any():
                        fig_fq.add_trace(go.Scatter(
                            x=vp["date"], y=vp["best_fis"],
                            mode="lines+markers", name="Winner FIS",
                            line=dict(color="#1f77b4", width=2),
                            text=vp["winner"],
                            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Winner FIS: %{y:.1f}<br>Winner: %{text}<extra></extra>",
                        ))
                    fig_fq.update_layout(
                        height=320, margin=dict(t=20, b=20),
                        yaxis=dict(autorange="reversed", title="FIS Points (lower = faster)"),
                        xaxis_title="Edition", legend=dict(orientation="h", y=1.08),
                    )
                    st.plotly_chart(fig_fq, use_container_width=True)

                st.markdown("---")

                # Field spread trend
                spread_data = vp.dropna(subset=["field_spread"])
                if len(spread_data) >= 2:
                    st.subheader("Field Spread Over Time")
                    st.caption("IQR of FIS points per edition — higher = more decisive race.")
                    fig_spread = go.Figure(go.Scatter(
                        x=spread_data["date"], y=spread_data["field_spread"],
                        mode="lines+markers",
                        line=dict(color="#9467bd", width=2),
                        hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Field Spread (IQR): %{y:.1f}<extra></extra>",
                    ))
                    fig_spread.update_layout(
                        height=280, margin=dict(t=10, b=20),
                        yaxis_title="FIS Points IQR", xaxis_title="Edition",
                    )
                    st.plotly_chart(fig_spread, use_container_width=True)

                st.markdown("---")

                # Race history table
                st.subheader("Race History")
                hist = vp[[
                    "date", "race_type", "sex", "homologation_number",
                    "vertical_drop", "gates_r1", "setter_r1",
                    "field_size", "avg_fis", "winner",
                ]].copy()
                hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
                hist["avg_fis"] = hist["avg_fis"].round(1)
                hist = hist.sort_values("date", ascending=False)
                hist.columns = [
                    "Date", "Type", "Sex", "Homologation",
                    "Vert (m)", "Gates R1", "Setter",
                    "Field", "Avg FIS", "Winner",
                ]
                st.dataframe(hist, use_container_width=True, hide_index=True)


# ===========================================================================
# COURSE SETTERS
# ===========================================================================

elif page == "Course Setters":
    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Course Setters** ranks the people who design and set the race courses — the individuals
            responsible for placing gates on the mountain.

            **Leaderboard columns:**
            - **Avg Gates** — average number of gates in their Run 1 courses
            - **Avg Vert (m)** — average vertical drop of venues where they've set
            - **Avg Field Spread** — average IQR of FIS points across their races.
              A higher number means finishes were more spread out — the course separated athletes more.
            - **Avg Winner Z** — average Z-score (standard deviations above field avg) of the race winner
              across all their races. Higher = winners dominated more decisively on their courses.

            **What the sorting options mean:**
            - *Most Races Set* = raw experience / volume
            - *Widest Field Spread* = their courses tend to produce more decisive outcomes
            - *Most Dominant Winners* = their courses allow top athletes to really pull away
            - *Most Gates / Most Vertical* = course style preferences

            **Setter Detail** shows every individual race they've set, with the winner and field quality.
            """
        )

    disc_setter_opts = ["All", "Slalom", "Giant Slalom", "Super G", "Downhill"]
    sel_disc_setter = st.sidebar.selectbox("Discipline", disc_setter_opts, key="setter_disc")
    min_races_setter = st.sidebar.number_input("Min races", min_value=1, value=5, step=1, key="setter_min")
    sort_by = st.sidebar.selectbox(
        "Sort by",
        ["races", "avg_field_spread", "avg_winner_z", "avg_gates", "avg_vert"],
        format_func=lambda x: {
            "races": "Most Races Set",
            "avg_field_spread": "Widest Field Spread",
            "avg_winner_z": "Most Dominant Winners",
            "avg_gates": "Most Gates",
            "avg_vert": "Most Vertical",
        }[x],
        key="setter_sort",
    )

    with st.spinner("Loading setter stats..."):
        try:
            setter_df = load_setter_leaderboard(
                sel_disc_setter if sel_disc_setter != "All" else None
            )
        except Exception as e:
            st.error(f"Error loading setter data: {e}")
            setter_df = pd.DataFrame()

    if not setter_df.empty:
        setter_df = (
            setter_df[setter_df["races"] >= min_races_setter]
            .sort_values(sort_by, ascending=False)
            .reset_index(drop=True)
        )

        st.subheader("Setter Leaderboard")
        st.caption(
            "**Avg Field Spread** = average IQR of FIS points — higher = more decisive outcomes. "
            "**Avg Winner Z** = average Z-score of race winners — higher = winners dominated more."
        )
        display_setter = setter_df.copy()
        display_setter.columns = [
            "Setter", "Country", "Races", "Avg Gates",
            "Avg Vert (m)", "Avg Field Spread", "Avg Winner Z",
        ]
        st.dataframe(display_setter, use_container_width=True, hide_index=True, height=420)

        st.markdown("---")
        st.subheader("Setter Detail")
        sel_setter = st.selectbox(
            "Select a setter to see all their races",
            setter_df["setter"].tolist(),
            key="setter_select",
        )
        if sel_setter:
            with st.spinner(f"Loading races for {sel_setter}..."):
                sr = load_setter_races(sel_setter)
            if not sr.empty:
                m1, m2, m3 = st.columns(3)
                m1.metric("Races Set", len(sr))
                m2.metric("Venues", sr["location"].nunique())
                m3.metric("Disciplines", sr["discipline"].nunique())
                sr_display = sr.copy()
                sr_display["date"] = sr_display["date"].dt.strftime("%Y-%m-%d")
                sr_display.columns = [
                    "Date", "Location", "Discipline", "Type", "Sex",
                    "Vert (m)", "Gates R1", "Field", "Avg FIS", "Winner",
                ]
                st.dataframe(sr_display, use_container_width=True, hide_index=True)
    else:
        st.info("No setter data found.")


# ===========================================================================
# BEST COURSES
# ===========================================================================

elif page == "Best Courses":
    disc_best_opts = ["All", "Slalom", "Giant Slalom", "Super G", "Downhill"]
    sel_disc_best = st.sidebar.selectbox("Discipline", disc_best_opts, key="best_disc")
    min_perf = st.sidebar.number_input(
        "Min performances (data quality)",
        min_value=1, value=3, step=1, key="best_min_perf",
    )
    hill_search_best = st.sidebar.text_input("Search hill", placeholder="e.g. Kitzbühel", key="best_hill_search")

    st.subheader("Best Courses")

    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Best Courses** ranks venues by how well athletes tend to perform there relative to the rest of the field.

            **How the score is calculated:**
            Each athlete's top 3 career performances are identified by Z-score — a measure of how far above
            the field average they finished (in standard deviations). For each course, the average Z-score
            across all athletes' top performances at that venue is computed. A higher mean Z-score means
            athletes consistently post their career-best results there.

            **What it means:**
            - A high score = this course brings out peak skiing — either the terrain, conditions, or course
              setting enables athletes to outperform the field more than usual.
            - A low score = consistent, tight finishes where it's hard to separate from the pack.
            - This is **not** a difficulty measure — it's a "peak performance" measure.

            **Homologation numbers:** A single mountain can have multiple course configurations (different
            start areas, course lines), each registered under a different homologation number. Results are
            broken out per configuration.

            > **Note on selection bias:** Because only each athlete's *top 3* career results are counted,
            > venues that host high-profile races (World Cup, World Championships) will naturally attract
            > more appearances in athletes' top-3 lists — not necessarily because the venue enables better
            > skiing, but because the strongest fields race there. Treat this ranking as a guide to where
            > athletes have historically peaked, not as a definitive measure of course quality.
            """
        )

    with st.spinner("Loading best course rankings..."):
        try:
            bc_df = load_best_courses(
                discipline=sel_disc_best if sel_disc_best != "All" else None,
                min_performances=min_perf,
            )
        except Exception as e:
            st.error(f"Error: {e}")
            bc_df = pd.DataFrame()

    if not bc_df.empty:
        if hill_search_best.strip():
            bc_df = bc_df[bc_df["location"].str.contains(hill_search_best.strip(), case=False, na=False)]
        bc_df = bc_df.reset_index(drop=True)
        top_n = min(30, len(bc_df))
        top_bc = bc_df.head(top_n).copy()
        top_bc["label"] = top_bc["location"] + " (" + top_bc["discipline"] + ")"

        # Main ranking chart — horizontal bar with color gradient
        st.subheader(f"Top {top_n} Courses by Mean Z-Score")
        st.caption(
            "Each bar = one course area (location + homologation). "
            "Longer bar = athletes tend to post higher Z-scores (further above field average) at this venue. "
            "Hover for course details."
        )
        fig_bc = go.Figure(go.Bar(
            x=top_bc["mean_z_score"],
            y=top_bc["label"],
            orientation="h",
            marker=dict(
                color=top_bc["mean_z_score"],
                colorscale=[[0, "#c6dbef"], [0.5, "#6baed6"], [1, "#084594"]],
                showscale=True,
                colorbar=dict(title="Mean Z-Score", thickness=14),
            ),
            text=top_bc["mean_z_score"].round(2),
            textposition="outside",
            customdata=top_bc[["homologation_number", "performance_count", "avg_vert", "avg_gates", "country"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Mean Z-Score: <b>%{x:.3f}</b><br>"
                "Homologation: %{customdata[0]}<br>"
                "Top performances: %{customdata[1]}<br>"
                "Country: %{customdata[4]}<br>"
                "Avg Vert: %{customdata[2]:.0f} m  ·  Avg Gates: %{customdata[3]:.1f}<extra></extra>"
            ),
        ))
        fig_bc.update_layout(
            height=max(440, top_n * 24),
            margin=dict(t=20, b=20, l=10, r=120),
            xaxis=dict(title="Mean Z-Score (higher = athletes peak here)", range=[0, None]),
            yaxis=dict(categoryorder="total ascending"),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_bc, use_container_width=True)

        st.markdown("---")

        # Scatter: Mean Z-Score vs Avg FIS (field quality)
        if "avg_fis" in bc_df.columns and bc_df["avg_fis"].notna().any():
            st.subheader("Performance vs Field Quality")
            st.caption(
                "X-axis = average FIS points of the field at this venue (lower = stronger field). "
                "Y-axis = mean Z-score. Top-right = strong field AND athletes still dominate. "
                "Size = number of top performances recorded."
            )
            sc_df = bc_df.dropna(subset=["avg_fis", "mean_z_score"]).copy()
            if not sc_df.empty:
                sc_df["label"] = sc_df["location"] + " (" + sc_df["discipline"] + ")"
                fig_sc = go.Figure(go.Scatter(
                    x=sc_df["avg_fis"],
                    y=sc_df["mean_z_score"],
                    mode="markers",
                    marker=dict(
                        size=sc_df["performance_count"].clip(upper=20) * 2.5 + 5,
                        color=sc_df["mean_z_score"],
                        colorscale="Blues",
                        showscale=False,
                        opacity=0.75,
                        line=dict(width=0.5, color="white"),
                    ),
                    text=sc_df["label"],
                    customdata=sc_df[["homologation_number", "performance_count", "avg_vert"]].values,
                    hovertemplate=(
                        "<b>%{text}</b><br>"
                        "Mean Z-Score: %{y:.3f}<br>"
                        "Avg Field FIS: %{x:.1f}<br>"
                        "Performances: %{customdata[1]}<br>"
                        "Avg Vert: %{customdata[2]:.0f} m<extra></extra>"
                    ),
                ))
                fig_sc.update_layout(
                    height=400, margin=dict(t=20, b=20),
                    xaxis=dict(title="Avg Field FIS (lower = stronger field)", autorange="reversed"),
                    yaxis_title="Mean Z-Score",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig_sc, use_container_width=True)

        st.markdown("---")
        st.subheader("Full Rankings Table")
        bc_table = bc_df[[
            "rank", "location", "discipline", "homologation_number",
            "country", "mean_z_score", "performance_count", "avg_vert", "avg_gates",
        ]].copy()
        bc_table["mean_z_score"] = bc_table["mean_z_score"].round(3)
        bc_table["avg_vert"]     = bc_table["avg_vert"].round(0)
        bc_table["avg_gates"]    = bc_table["avg_gates"].round(1)
        bc_table.columns = [
            "Rank", "Location", "Discipline", "Homologation",
            "Country", "Mean Z-Score", "Performances", "Avg Vert (m)", "Avg Gates",
        ]
        st.dataframe(bc_table, use_container_width=True, hide_index=True, height=500)
    else:
        st.info("No best course data found for those filters.")


# ===========================================================================
# HILL DIFFICULTY (HDI)
# ===========================================================================

elif page == "Hill Difficulty (HDI)":
    disc_hdi_opts = ["All", "Slalom", "Giant Slalom", "Super G", "Downhill"]
    sel_disc_hdi  = st.sidebar.selectbox("Discipline", disc_hdi_opts, key="hdi_disc")
    min_races_hdi = st.sidebar.number_input("Min races", min_value=1, value=2, step=1, key="hdi_min_races")
    show_n        = st.sidebar.number_input("Show top N", min_value=5, max_value=100, value=30, step=5, key="hdi_n")
    hill_search_hdi = st.sidebar.text_input("Search hill", placeholder="e.g. Val Gardena", key="hdi_hill_search")

    st.subheader("Hill Difficulty Index (HDI)")

    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Hill Difficulty Index (HDI)** is a composite score from 0–100 that measures how physically and
            technically demanding a course is. Higher = harder.

            **How it's calculated (weighted score):**
            | Component | Weight | What it measures |
            |---|---|---|
            | DNF Rate | **40%** | Attrition — how often athletes fail to finish |
            | Vertical Drop | **20%** | Elevation change from start to finish |
            | Winning Time | **20%** | Longer times = more demanding course |
            | Gate Count | **10%** | More gates = more technical |
            | Start Altitude | **10%** | Higher starts = thinner air, more exposed conditions |

            Each component is normalized on a 0–100 scale **within discipline**, so an SL and a DH score
            are only comparable within their own event type.

            **What it means:**
            - A high HDI course challenges even the best athletes — high attrition, long runs, demanding terrain.
            - A low HDI course produces cleaner, faster, more predictable racing.
            - DNF rate carries the most weight because it directly captures whether a course is punishing errors.

            **Homologation numbers:** Each course configuration on a mountain gets its own homologation number
            and its own HDI score.

            > **Note on DNF sensitivity:** DNF rate accounts for 40% of the score, which means a single
            > race with an unusually high attrition (e.g., extreme weather, one-off conditions) can
            > significantly elevate a course's HDI — especially for venues with fewer races in the database.
            > The "Min races" filter in the sidebar helps reduce this noise. Treat HDI as a long-run
            > average, not a judgment on any single race edition.
            """
        )

    with st.spinner("Loading HDI data..."):
        try:
            hdi_df = load_hdi(
                discipline=sel_disc_hdi if sel_disc_hdi != "All" else None,
                min_races=min_races_hdi,
            )
        except Exception as e:
            st.error(f"Error: {e}")
            hdi_df = pd.DataFrame()

    if not hdi_df.empty:
        if hill_search_hdi.strip():
            hdi_df = hdi_df[hdi_df["location"].str.contains(hill_search_hdi.strip(), case=False, na=False)]
        top_hdi = hdi_df.head(int(show_n)).copy()
        top_hdi["label"] = top_hdi["location"] + " (" + top_hdi["discipline"] + ")"

        # Main ranking bar chart
        st.subheader(f"Top {int(show_n)} Hardest Courses")
        st.caption(
            "Each bar = one course configuration (location + homologation number). "
            "Darker red = harder. Hover for component breakdown."
        )
        fig_hdi = go.Figure(go.Bar(
            x=top_hdi["hdi"],
            y=top_hdi["label"],
            orientation="h",
            marker=dict(
                color=top_hdi["hdi"],
                colorscale=[[0, "#fcbba1"], [0.5, "#fb6a4a"], [1, "#67000d"]],
                showscale=True,
                colorbar=dict(title="HDI", thickness=14),
            ),
            text=top_hdi["hdi"].round(1),
            textposition="outside",
            customdata=top_hdi[[
                "homologation_number", "race_count",
                "avg_vertical_drop", "avg_gate_count",
                "dnf_pct", "avg_winning_time",
            ]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "HDI: <b>%{x:.1f}</b><br>"
                "Homologation: %{customdata[0]}<br>"
                "Races analysed: %{customdata[1]}<br>"
                "─────────────────<br>"
                "DNF Rate: %{customdata[4]:.1f}%  (40% weight)<br>"
                "Vert Drop: %{customdata[2]:.0f} m  (20% weight)<br>"
                "Avg Win Time: %{customdata[5]}  (20% weight)<br>"
                "Avg Gates: %{customdata[3]:.1f}  (10% weight)<extra></extra>"
            ),
        ))
        fig_hdi.update_layout(
            height=max(440, int(show_n) * 24),
            margin=dict(t=20, b=20, l=10, r=100),
            xaxis=dict(title="Hill Difficulty Index (higher = harder)", range=[0, 108]),
            yaxis=dict(categoryorder="total ascending"),
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_hdi, use_container_width=True)

        st.markdown("---")

        # Component breakdown — radar chart for selected hill
        st.subheader("Component Breakdown")
        st.caption(
            "Select a hill to see a radar chart of its five HDI components, "
            "each normalized 0–100 within its discipline. "
            "The shaded area shows how this course scores on each axis — "
            "a large shape = hard across multiple dimensions."
        )
        sel_hill_label = st.selectbox(
            "Select a hill",
            top_hdi["label"].tolist(),
            key="hdi_hill_select",
        )
        if sel_hill_label:
            sel_row = top_hdi[top_hdi["label"] == sel_hill_label].iloc[0]
            detail = load_hdi_detail(
                sel_row["location"], sel_row["discipline"], sel_row["homologation_number"]
            )

            # Metrics row
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("HDI Score",    f"{sel_row['hdi']:.1f}",
                      help="Overall composite score 0–100")
            c2.metric("DNF Rate",     f"{sel_row['dnf_pct']:.1f}%",
                      help="40% of HDI — attrition rate")
            c3.metric("Vert Drop",    f"{sel_row['avg_vertical_drop']:.0f} m",
                      help="20% of HDI")
            c4.metric("Avg Win Time", sel_row["avg_winning_time"] or "—",
                      help="20% of HDI — longer = harder")
            c5.metric("Avg Gates",    f"{sel_row['avg_gate_count']:.1f}" if sel_row["avg_gate_count"] else "—",
                      help="10% of HDI")

            if detail:
                components = ["DNF Rate\n(40%)", "Vert Drop\n(20%)", "Win Time\n(20%)", "Gates\n(10%)", "Start Alt\n(10%)"]
                values = [
                    detail.get("dnf_rate_norm", 0),
                    detail.get("vertical_drop_norm", 0),
                    detail.get("winning_time_norm", 0),
                    detail.get("gate_count_norm", 0),
                    detail.get("start_altitude_norm", 0),
                ]
                # Close the radar polygon
                fig_radar = go.Figure(go.Scatterpolar(
                    r=values + [values[0]],
                    theta=components + [components[0]],
                    fill="toself",
                    fillcolor="rgba(214,39,40,0.25)",
                    line=dict(color="#d62728", width=2),
                    name=sel_hill_label,
                    hovertemplate="%{theta}: %{r:.1f}<extra></extra>",
                ))
                fig_radar.update_layout(
                    polar=dict(
                        radialaxis=dict(visible=True, range=[0, 100], tickfont_size=10),
                        angularaxis=dict(tickfont_size=11),
                    ),
                    height=380,
                    margin=dict(t=40, b=40, l=60, r=60),
                    showlegend=False,
                )
                st.plotly_chart(fig_radar, use_container_width=True)

        st.markdown("---")
        st.subheader("Full HDI Table")
        hdi_table = hdi_df[[
            "location", "discipline", "homologation_number", "country",
            "race_count", "hdi", "avg_vertical_drop",
            "avg_gate_count", "avg_start_altitude", "dnf_pct", "avg_winning_time",
        ]].copy()
        hdi_table["avg_vertical_drop"]  = hdi_table["avg_vertical_drop"].round(0)
        hdi_table["avg_gate_count"]     = hdi_table["avg_gate_count"].round(1)
        hdi_table["avg_start_altitude"] = hdi_table["avg_start_altitude"].round(0)
        hdi_table["hdi"]                = hdi_table["hdi"].round(1)
        hdi_table.columns = [
            "Location", "Discipline", "Homologation", "Country",
            "Races", "HDI", "Avg Vert (m)",
            "Avg Gates", "Start Alt (m)", "DNF %", "Avg Win Time",
        ]
        st.dataframe(hdi_table, use_container_width=True, hide_index=True, height=500)
    else:
        st.info("No HDI data found for those filters.")


# ===========================================================================
# COURSE SIMILARITY
# ===========================================================================

elif page == "Course Similarity":
    st.subheader("Course Similarity")

    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Course Similarity** finds the 10 most similar courses to any reference venue, based on
            how closely their physical and performance characteristics match.

            **Similarity is measured across 6 dimensions:**
            - Average winning time
            - Average gate count
            - Average start altitude
            - Average vertical drop
            - DNF rate
            - Average field FIS points (competition level)

            **Similarity Score** = sum of absolute percentage differences across all dimensions.
            **Lower score = more similar.** A score of 0 would be a perfect match.
            Courses where any single metric differs by more than 50% are excluded.

            **Use cases:**
            - Find comparable training venues before racing at an unfamiliar hill
            - Identify which courses in your region resemble a World Cup venue
            - Understand which hills produce similar race outcomes

            **Scope:** Similarity is computed within the same discipline. Use the country filter
            in the sidebar to narrow results to a specific nation.
            """
        )

    if venue_list.empty:
        st.info("No venue data available.")
    else:
        all_disc_sim = sorted(venue_list["discipline"].dropna().unique().tolist())
        sel_disc_sim = st.sidebar.selectbox("Discipline", all_disc_sim, key="sim_disc")

        venues_sim = sorted(
            venue_list[venue_list["discipline"] == sel_disc_sim]["location"]
            .dropna().unique().tolist()
        )
        loc_search_sim = st.sidebar.text_input("Search venue", placeholder="e.g. Killington", key="sim_loc_search")
        if loc_search_sim.strip():
            venues_sim = [v for v in venues_sim if loc_search_sim.strip().lower() in v.lower()]

        sel_loc_sim = st.sidebar.selectbox("Reference venue", venues_sim, key="sim_loc")

        # Optional country filter for results
        country_filter_sim = st.sidebar.text_input(
            "Filter results by country (optional)",
            placeholder="e.g. AUT",
            key="sim_country_filter",
            help="Leave blank to see all countries. Enter a country code (e.g. AUT, SUI, USA) to narrow results.",
        )

        if sel_loc_sim:
            # Load venue profile to get available homologation numbers
            with st.spinner("Loading venue..."):
                vp_sim = load_venue_profile(sel_loc_sim, sel_disc_sim)

            if not vp_sim.empty:
                homos_sim = sorted(vp_sim["homologation_number"].dropna().unique().tolist())
                sel_homo_sim = st.sidebar.selectbox(
                    "Homologation number",
                    homos_sim,
                    key="sim_homo",
                    help="Select which course configuration to use as the reference.",
                )

                with st.spinner("Finding similar courses..."):
                    sim_df = load_similar_courses(sel_loc_sim, sel_disc_sim, sel_homo_sim)

                st.markdown(f"**Reference course:** {sel_loc_sim} · {sel_disc_sim} · `{sel_homo_sim}`")

                # Reference course stats from venue profile
                ref_rows = vp_sim[vp_sim["homologation_number"] == sel_homo_sim]
                if not ref_rows.empty:
                    rr = ref_rows.iloc[0]
                    rc1, rc2, rc3, rc4 = st.columns(4)
                    rc1.metric("Vertical Drop", f"{int(rr['vertical_drop'])} m" if pd.notna(rr.get("vertical_drop")) else "—")
                    rc2.metric("Start Altitude", f"{int(rr['start_altitude'])} m" if pd.notna(rr.get("start_altitude")) else "—")
                    rc3.metric("Gates R1", f"{int(rr['gates_r1'])}" if pd.notna(rr.get("gates_r1")) else "—")
                    rc4.metric("Avg Field FIS", f"{rr['avg_fis']:.1f}" if pd.notna(rr.get("avg_fis")) else "—")

                st.markdown("---")

                # Apply optional country filter
                if country_filter_sim.strip() and not sim_df.empty and "country" in sim_df.columns:
                    sim_df = sim_df[
                        sim_df["country"].str.upper() == country_filter_sim.strip().upper()
                    ]

                if sim_df.empty:
                    st.info(
                        "No similar courses found in the database for this course configuration"
                        + (f" matching country '{country_filter_sim.strip().upper()}'." if country_filter_sim.strip() else ".")
                    )
                else:
                    st.subheader("Top 10 Most Similar Courses")
                    st.caption(
                        "**Similarity Score** = sum of absolute % differences across winning time, "
                        "gate count, start altitude, vertical drop, DNF rate, and field FIS points. "
                        "Lower = more similar. Similarity is computed within the same discipline."
                        + (f" Results filtered to country: **{country_filter_sim.strip().upper()}**." if country_filter_sim.strip() else "")
                    )

                    # Bar chart of similarity scores
                    fig_sim = go.Figure(go.Bar(
                        x=sim_df["similarity_score"],
                        y=sim_df["location"] + " (" + sim_df["homologation_number"] + ")",
                        orientation="h",
                        marker=dict(
                            color=sim_df["similarity_score"],
                            colorscale=[[0, "#006d2c"], [0.5, "#74c476"], [1, "#edf8e9"]],
                            reversescale=True,
                            showscale=False,
                        ),
                        text=sim_df["similarity_score"].round(2),
                        textposition="outside",
                        customdata=sim_df[[
                            "avg_vert", "avg_gates", "avg_start_alt", "dnf_pct", "avg_fis"
                        ]].values,
                        hovertemplate=(
                            "<b>%{y}</b><br>"
                            "Similarity Score: <b>%{x:.3f}</b>  (lower = more similar)<br>"
                            "─────────────────<br>"
                            "Avg Vert: %{customdata[0]:.0f} m<br>"
                            "Avg Gates: %{customdata[1]:.1f}<br>"
                            "Start Alt: %{customdata[2]:.0f} m<br>"
                            "DNF Rate: %{customdata[3]:.1f}%<br>"
                            "Avg Field FIS: %{customdata[4]:.1f}<extra></extra>"
                        ),
                    ))
                    fig_sim.update_layout(
                        height=380,
                        margin=dict(t=20, b=20, l=10, r=80),
                        xaxis_title="Similarity Score (lower = more similar)",
                        yaxis=dict(categoryorder="total ascending"),
                        plot_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(fig_sim, use_container_width=True)

                    st.markdown("---")

                    # Radar comparison: reference vs top similar
                    st.subheader("Characteristic Comparison")
                    st.caption(
                        "Radar chart comparing the reference course (blue) against a selected similar course (green) "
                        "across key physical dimensions."
                    )
                    sel_similar = st.selectbox(
                        "Compare against",
                        (sim_df["location"] + " — " + sim_df["homologation_number"]).tolist(),
                        key="sim_compare_select",
                    )
                    if sel_similar and not ref_rows.empty:
                        sim_idx = (sim_df["location"] + " — " + sim_df["homologation_number"]).tolist().index(sel_similar)
                        sim_row = sim_df.iloc[sim_idx]

                        # Normalize both courses for comparison (use sim row values directly as they're already averages)
                        ref_row_avg = ref_rows[["vertical_drop", "gates_r1", "start_altitude"]].mean()
                        dims = ["Vert Drop (m)", "Gates R1", "Start Alt (m)", "DNF %", "Avg Field FIS"]
                        ref_vals = [
                            ref_row_avg.get("vertical_drop", 0) or 0,
                            ref_row_avg.get("gates_r1", 0) or 0,
                            ref_row_avg.get("start_altitude", 0) or 0,
                            0,  # DNF not directly in venue profile
                            ref_rows["avg_fis"].mean() or 0,
                        ]
                        sim_vals = [
                            sim_row["avg_vert"] or 0,
                            sim_row["avg_gates"] or 0,
                            sim_row["avg_start_alt"] or 0,
                            sim_row["dnf_pct"] or 0,
                            sim_row["avg_fis"] or 0,
                        ]

                        fig_cmp_radar = go.Figure()
                        fig_cmp_radar.add_trace(go.Scatterpolar(
                            r=ref_vals + [ref_vals[0]],
                            theta=dims + [dims[0]],
                            fill="toself",
                            fillcolor="rgba(31,119,180,0.2)",
                            line=dict(color="#1f77b4", width=2),
                            name=f"{sel_loc_sim} (ref)",
                        ))
                        fig_cmp_radar.add_trace(go.Scatterpolar(
                            r=sim_vals + [sim_vals[0]],
                            theta=dims + [dims[0]],
                            fill="toself",
                            fillcolor="rgba(44,160,44,0.2)",
                            line=dict(color="#2ca02c", width=2),
                            name=sel_similar.split(" — ")[0],
                        ))
                        fig_cmp_radar.update_layout(
                            polar=dict(radialaxis=dict(visible=True, tickfont_size=9)),
                            height=380,
                            margin=dict(t=40, b=40, l=60, r=60),
                            legend=dict(orientation="h", y=-0.1),
                        )
                        st.plotly_chart(fig_cmp_radar, use_container_width=True)

                    st.markdown("---")
                    st.subheader("Similar Courses Table")
                    sim_table = sim_df.copy()
                    sim_table.columns = [
                        "Location", "Homologation", "Similarity Score",
                        "Avg Win Time (min)", "Avg Gates", "Start Alt (m)",
                        "Avg Vert (m)", "DNF %", "Avg Field FIS", "Country",
                    ]
                    st.dataframe(sim_table, use_container_width=True, hide_index=True)
            else:
                st.info("No data found for that venue.")


# ===========================================================================
# VENUE COMPARISON
# ===========================================================================

elif page == "Venue Comparison":
    with st.expander("What does this show?", expanded=False):
        st.markdown(
            """
            **Venue Comparison** lets you put up to 4 venues side by side for the same discipline.

            - **Summary table** — editions, course areas (homologation count), average vert,
              gates, field FIS, best FIS ever recorded, field size, and average field spread.
            - **Field Quality Trend** — line chart of avg field FIS over time for each venue.
              See which events attract stronger fields and whether that's changed over the years.
            - **Average Gate Count** — a quick bar chart comparing how many gates each venue
              typically sets.

            Use this to understand how venues stack up against each other — useful for
            pre-season planning or identifying comparable events for training targeting.
            """
        )

    comp_disc = st.sidebar.selectbox(
        "Discipline",
        sorted(venue_list["discipline"].dropna().unique()),
        key="comp_disc",
    )
    comp_venue_opts = sorted(
        venue_list[venue_list["discipline"] == comp_disc]["location"]
        .dropna().unique().tolist()
    )
    sel_venues = st.sidebar.multiselect(
        "Select up to 4 venues",
        comp_venue_opts,
        max_selections=4,
        key="comp_venues",
    )

    st.subheader("Venue Comparison")

    if sel_venues:
        all_vp = []
        for v in sel_venues:
            with st.spinner(f"Loading {v}..."):
                vdf = load_venue_profile(v, comp_disc)
            if not vdf.empty:
                vdf["venue"] = v
                all_vp.append(vdf)

        if all_vp:
            combined = pd.concat(all_vp, ignore_index=True)

            summary_rows = []
            for v in sel_venues:
                sub = combined[combined["venue"] == v]
                homos = sub["homologation_number"].dropna().unique().tolist()
                summary_rows.append({
                    "Venue": v,
                    "Editions": len(sub),
                    "Homologations": len(homos),
                    "Avg Vert (m)": round(sub["vertical_drop"].mean(), 0) if sub["vertical_drop"].notna().any() else None,
                    "Avg Gates R1": round(sub["gates_r1"].mean(), 1) if sub["gates_r1"].notna().any() else None,
                    "Avg Field FIS": round(sub["avg_fis"].mean(), 1) if sub["avg_fis"].notna().any() else None,
                    "Best FIS Ever": round(sub["best_fis"].min(), 1) if sub["best_fis"].notna().any() else None,
                    "Avg Field Size": round(sub["field_size"].mean(), 0) if sub["field_size"].notna().any() else None,
                    "Avg Field Spread": round(sub["field_spread"].mean(), 1) if sub["field_spread"].notna().any() else None,
                })
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            if combined["avg_fis"].notna().any():
                st.markdown("---")
                st.subheader("Field Quality Trend")
                fig_cmp = px.line(
                    combined.sort_values("date"),
                    x="date", y="avg_fis", color="venue", markers=True,
                    labels={"date": "Date", "avg_fis": "Field Avg FIS", "venue": "Venue"},
                )
                fig_cmp.update_layout(
                    height=380, margin=dict(t=20, b=20),
                    yaxis=dict(autorange="reversed", title="Field Avg FIS (lower = stronger field)"),
                )
                st.plotly_chart(fig_cmp, use_container_width=True)

            gates_rows = [
                {"Venue": v, "Avg Gates R1": round(combined[combined["venue"] == v]["gates_r1"].dropna().mean(), 1)}
                for v in sel_venues
                if not combined[combined["venue"] == v]["gates_r1"].dropna().empty
            ]
            if gates_rows:
                st.markdown("---")
                st.subheader("Average Gate Count")
                fig_gc = px.bar(
                    pd.DataFrame(gates_rows), x="Venue", y="Avg Gates R1",
                    color="Venue", text="Avg Gates R1",
                )
                fig_gc.update_traces(textposition="outside")
                fig_gc.update_layout(height=320, margin=dict(t=20, b=20), showlegend=False)
                st.plotly_chart(fig_gc, use_container_width=True)
    else:
        st.info("Select venues in the sidebar to compare.")


# ===========================================================================
# COURSE SEARCH
# ===========================================================================

elif page == "Course Search":
    st.subheader("Search Races by Course Characteristics")
    st.caption("Filter the full race database by any combination of course specs. Returns up to 500 results.")

    disc_all_search = [
        "Slalom", "Giant Slalom", "Super G", "Downhill",
        "Alpine combined", "Alpine Combined", "Parallel Slalom",
    ]
    sel_discs = st.sidebar.multiselect(
        "Discipline", disc_all_search,
        default=["Slalom", "Giant Slalom"],
        key="search_disc",
    )
    race_type_opts = [
        "FIS", "Nor-Am Cup", "National Championships", "National Junior Race",
        "European Cup", "World Cup", "Audi FIS Ski World Cup",
        "CIT", "Junior", "University",
    ]
    sel_types = st.sidebar.multiselect("Race Type", race_type_opts, key="search_type")

    st.sidebar.markdown("**Vertical drop (m)**")
    sv1, sv2 = st.sidebar.columns(2)
    min_vert  = sv1.number_input("Min", min_value=0, max_value=2000, value=0, step=50, key="s_min_vert", label_visibility="collapsed")
    max_vert  = sv2.number_input("Max", min_value=0, max_value=2000, value=0, step=50, key="s_max_vert", label_visibility="collapsed")

    st.sidebar.markdown("**Gates R1**")
    sg1, sg2 = st.sidebar.columns(2)
    min_gates = sg1.number_input("Min", min_value=0, max_value=200, value=0, step=5, key="s_min_gates", label_visibility="collapsed")
    max_gates = sg2.number_input("Max", min_value=0, max_value=200, value=0, step=5, key="s_max_gates", label_visibility="collapsed")

    date_from = st.sidebar.date_input("From date", value=None, key="s_date_from")
    date_to   = st.sidebar.date_input("To date",   value=None, key="s_date_to")

    if st.sidebar.button("Search", type="primary", key="search_btn"):
        with st.spinner("Searching..."):
            try:
                results = load_course_search(
                    disciplines=tuple(sel_discs) if sel_discs else (),
                    min_vert=min_vert if min_vert > 0 else None,
                    max_vert=max_vert if max_vert > 0 else None,
                    min_gates=min_gates if min_gates > 0 else None,
                    max_gates=max_gates if max_gates > 0 else None,
                    race_types=tuple(sel_types) if sel_types else (),
                    date_from=date_from,
                    date_to=date_to,
                )
            except Exception as e:
                st.error(f"Search error: {e}")
                results = pd.DataFrame()

        if not results.empty:
            st.success(f"Found {len(results)} races (showing up to 500)")
            res = results.copy()
            res["date"] = res["date"].dt.strftime("%Y-%m-%d")
            res.columns = [
                "Date", "Location", "Discipline", "Type", "Sex", "Country",
                "Vert (m)", "Start Alt", "Gates R1", "Turning R1", "Setter R1",
                "Field Size", "Avg FIS",
            ]
            st.dataframe(res, use_container_width=True, hide_index=True, height=520)
        else:
            st.info("No races found matching those criteria.")
    else:
        st.info("Set filters in the sidebar and click Search.")
