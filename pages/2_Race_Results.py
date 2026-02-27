"""
Alpine Analytics — Race Results Browser
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
def load_race_index() -> pd.DataFrame:
    """All races with summary stats — one row per race_id so M/W and double-headers stay separate."""
    df = query("""
        SELECT
            sg.race_id,
            sg.date,
            sg.discipline,
            sg.location,
            rd.sex,
            rd.race_type,
            COUNT(*)                                                        AS field_size,
            AVG(sg.fis_points)                                              AS avg_fis,
            MIN(sg.fis_points)                                              AS best_fis,
            STDDEV(sg.fis_points)                                           AS stddev_fis,
            PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY sg.fis_points)    AS p25_fis,
            PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY sg.fis_points)    AS p75_fis
        FROM race_aggregate.strokes_gained sg
        LEFT JOIN raw.race_details rd ON rd.race_id = sg.race_id
        WHERE sg.location IS NOT NULL
        GROUP BY sg.race_id, sg.date, sg.discipline, sg.location, rd.sex, rd.race_type
        ORDER BY sg.date DESC
    """)
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=86400)
def load_race_results(race_id: str) -> pd.DataFrame:
    """Full field for a specific race, sorted best-to-worst."""
    return query("""
        SELECT name, fis_points, points_gained, race_z_score
        FROM race_aggregate.strokes_gained
        WHERE race_id = :race_id
        ORDER BY points_gained DESC
    """, {"race_id": race_id})


@st.cache_data(ttl=86400)
def load_race_info(race_id: str) -> dict:
    """Race metadata from raw.race_details for a specific race_id."""
    try:
        df = query("""
            WITH counts AS (
                SELECT
                    COUNT(*)                                                        AS starters,
                    COUNT(CASE WHEN rank IS NOT NULL AND rank != '' THEN 1 END)    AS finishers
                FROM raw.fis_results
                WHERE race_id = :race_id
            )
            SELECT
                rd.race_type,
                rd.sex,
                rd.country,
                rd.vertical_drop,
                rd.start_altitude,
                rd.finish_altitude,
                rd.homologation_number,
                rd.first_run_course_setter,
                rd.first_run_course_setter_country,
                rd.first_run_number_of_gates,
                rd.first_run_turning_gates,
                rd.first_run_start_time,
                rd.second_run_course_setter,
                rd.second_run_course_setter_country,
                rd.second_run_number_of_gates,
                rd.second_run_turning_gates,
                rd.second_run_start_time,
                c.starters,
                c.finishers
            FROM raw.race_details rd
            CROSS JOIN counts c
            WHERE rd.race_id = :race_id
        """, {"race_id": race_id})
        if not df.empty:
            return df.iloc[0].to_dict()
    except Exception:
        pass
    return {}


@st.cache_data(ttl=86400)
def load_race_raw_results(race_id: str) -> pd.DataFrame:
    """Bib, country, run times from raw.fis_results for a specific race_id."""
    try:
        return query("""
            SELECT bib, name, country, rank, run1, run2, final_time, cup_points
            FROM raw.fis_results
            WHERE race_id = :race_id
            ORDER BY
                CASE WHEN rank ~ '^[0-9]+$' THEN rank::int ELSE 9999 END,
                bib
        """, {"race_id": race_id})
    except Exception:
        return pd.DataFrame()



# ---------------------------------------------------------------------------
# Sidebar — filters + race selector
# ---------------------------------------------------------------------------

st.sidebar.title("Race Browser")

try:
    race_index = load_race_index()
except Exception as e:
    st.error(f"Could not connect to database: {e}")
    st.stop()

if race_index.empty:
    st.warning("No race data found. Run the ETL pipeline first.")
    st.stop()

race_index["year"] = race_index["date"].dt.year

# Discipline filter
all_discs = sorted(race_index["discipline"].dropna().unique().tolist())
selected_discs = st.sidebar.multiselect("Discipline", all_discs, default=all_discs)

# Season filter
all_years = sorted(race_index["year"].unique().tolist(), reverse=True)
selected_year = st.sidebar.selectbox("Season", ["All seasons"] + [str(y) for y in all_years])

# Location search
location_search = st.sidebar.text_input("Search location", placeholder="e.g. Wengen")

st.sidebar.markdown("---")

# Apply filters
filtered = race_index[race_index["discipline"].isin(selected_discs)].copy()
if selected_year != "All seasons":
    filtered = filtered[filtered["year"] == int(selected_year)]
if location_search.strip():
    filtered = filtered[
        filtered["location"].str.contains(location_search.strip(), case=False, na=False)
    ]

if filtered.empty:
    st.warning("No races match the selected filters.")
    st.stop()

# Build race labels — include sex so M/W races on the same day are distinct
def _race_label(row) -> str:
    label = f"{row['date'].strftime('%Y-%m-%d')} · {row['location']} ({row['discipline']}"
    if pd.notna(row.get("sex")) and row["sex"]:
        label += f" · {row['sex']}"
    label += ")"
    return label

race_labels = [_race_label(row) for _, row in filtered.iterrows()]

selected_label = st.sidebar.selectbox("Select race", race_labels)
selected_idx   = race_labels.index(selected_label)
selected_race  = filtered.iloc[selected_idx]
selected_race_id = int(selected_race["race_id"])

# ---------------------------------------------------------------------------
# Load race results
# ---------------------------------------------------------------------------

with st.spinner("Loading race results..."):
    race_df   = load_race_results(selected_race_id)
    race_info = load_race_info(selected_race_id)
    raw_df    = load_race_raw_results(selected_race_id)

if race_df.empty:
    st.warning("No results found for this race.")
    st.stop()

race_df = race_df.reset_index(drop=True)
race_df["finish"] = race_df.index + 1

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

h1, h2 = st.columns([5, 1])
h1.title(selected_race["location"])
discipline_line = f"**{selected_race['discipline']}  ·  {selected_race['date'].strftime('%B %d, %Y')}**"
if race_info.get("race_type"):
    sex_label = f" · {race_info['sex']}" if race_info.get("sex") else ""
    discipline_line += f"  ·  *{race_info['race_type']}{sex_label}*"
h1.markdown(discipline_line)

st.markdown("---")

# Metrics row
starters = race_info.get("starters")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Starters", int(starters) if starters else int(selected_race["field_size"]))
m2.metric("Field Avg FIS", f"{selected_race['avg_fis']:.1f}")
m3.metric("Best FIS", f"{selected_race['best_fis']:.1f}")
spread = selected_race["p75_fis"] - selected_race["p25_fis"]
m4.metric("Field Spread (IQR)", f"{spread:.1f}",
          help="Interquartile range of FIS points — larger = more spread out field")

# Race info block — course details
if race_info:
    has_two_runs = race_info.get("second_run_course_setter") or race_info.get("second_run_number_of_gates")

    with st.container(border=True):
        info_cols = st.columns([2, 2, 1])

        # Run 1
        with info_cols[0]:
            run1_label = "Run 1" if has_two_runs else "Course"
            st.markdown(f"**{run1_label}**")
            if race_info.get("first_run_course_setter"):
                csc = race_info["first_run_course_setter"]
                csc_country = race_info.get("first_run_course_setter_country") or ""
                st.caption(f"Setter: {csc} ({csc_country})" if csc_country else f"Setter: {csc}")
            g1 = race_info.get("first_run_number_of_gates")
            t1 = race_info.get("first_run_turning_gates")
            if g1:
                st.caption(f"Gates: {int(g1)}" + (f"  ·  Turning: {int(t1)}" if t1 else ""))
            if race_info.get("first_run_start_time"):
                st.caption(f"Start: {race_info['first_run_start_time']}")

        # Run 2 (only if it exists)
        with info_cols[1]:
            if has_two_runs:
                st.markdown("**Run 2**")
                if race_info.get("second_run_course_setter"):
                    csc2 = race_info["second_run_course_setter"]
                    csc2_country = race_info.get("second_run_course_setter_country") or ""
                    st.caption(f"Setter: {csc2} ({csc2_country})" if csc2_country else f"Setter: {csc2}")
                g2 = race_info.get("second_run_number_of_gates")
                t2 = race_info.get("second_run_turning_gates")
                if g2:
                    st.caption(f"Gates: {int(g2)}" + (f"  ·  Turning: {int(t2)}" if t2 else ""))
                if race_info.get("second_run_start_time"):
                    st.caption(f"Start: {race_info['second_run_start_time']}")

        # Course stats
        with info_cols[2]:
            st.markdown("**Course**")
            if race_info.get("vertical_drop"):
                st.caption(f"Vert: {int(race_info['vertical_drop'])} m")
            if race_info.get("start_altitude") and race_info.get("finish_altitude"):
                st.caption(f"Alt: {int(race_info['start_altitude'])} → {int(race_info['finish_altitude'])} m")
            if race_info.get("homologation_number"):
                st.caption(f"Homol: {race_info['homologation_number']}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Podium
# ---------------------------------------------------------------------------

st.subheader("Podium")
st.caption(
    "Top 3 finishers by points gained vs the field average. "
    "FIS Pts = the athlete's FIS points in this race (lower = better result). "
    "Pts Gained = how far ahead of the field average they finished. "
    "Z-Score = performance in standard deviations vs the field."
)

top3 = race_df.head(3)
podium_cols = st.columns(min(3, len(top3)))
for col, label, (_, row) in zip(podium_cols, ["1st", "2nd", "3rd"], top3.iterrows()):
    with col:
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.markdown(f"### {row['name']}")
            c1, c2 = st.columns(2)
            c1.metric("FIS Pts", f"{row['fis_points']:.1f}")
            c2.metric("Pts Gained", f"{row['points_gained']:+.1f}")
            st.metric("Z-Score", f"{row['race_z_score']:.3f}")

st.markdown("---")

# ---------------------------------------------------------------------------
# Expected vs Actual — FIS Points vs Points Gained scatter
# ---------------------------------------------------------------------------

st.subheader("FIS Points vs Points Gained")
st.caption(
    "Each dot is one finisher. "
    "X-axis = FIS points in this race (lower = better finish). "
    "Y-axis = how many points the athlete gained relative to the field average — "
    "positive means they outperformed the field, negative means they fell behind. "
    "Blue dots are above-average performers; red dots are below. "
    "The dashed line at 0 marks the field average."
)

dot_colors = ["#1f77b4" if v > 0 else "#d62728" for v in race_df["points_gained"]]

fig_scatter = go.Figure(go.Scatter(
    x=race_df["fis_points"],
    y=race_df["points_gained"],
    mode="markers",
    marker=dict(
        color=dot_colors,
        size=10,
        opacity=0.8,
        line=dict(width=0.5, color="white"),
    ),
    text=race_df["name"],
    customdata=race_df[["finish", "race_z_score"]].values,
    hovertemplate=(
        "<b>%{text}</b><br>"
        "FIS Pts: %{x:.1f}<br>"
        "Pts Gained: %{y:+.1f}<br>"
        "Z-Score: %{customdata[1]:.3f}<br>"
        "Finish: %{customdata[0]}<extra></extra>"
    ),
))
fig_scatter.add_hline(y=0, line_dash="dot", line_color="gray")
fig_scatter.update_layout(
    height=400,
    margin=dict(t=20, b=0),
    xaxis_title="FIS Points (lower = better result)",
    yaxis_title="Points Gained vs Field Average",
)
st.plotly_chart(fig_scatter, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Field Performance Distribution — Z-Score bar chart
# ---------------------------------------------------------------------------

st.subheader("Field Performance Distribution")
st.caption(
    "Every finisher in the race, ordered from best to worst (left to right). "
    "Bar height = Z-Score vs the field. "
    "Blue = above the field average; red = below. "
    "A tall blue bar on the left means the winner dominated; "
    "a shallow spread across the field means a tight, contested race."
)

bar_colors_z = ["#1f77b4" if v >= 0 else "#d62728" for v in race_df["race_z_score"].fillna(0)]
fig_dist = go.Figure(go.Bar(
    x=race_df["name"],
    y=race_df["race_z_score"],
    marker_color=bar_colors_z,
    customdata=race_df[["finish", "fis_points", "points_gained"]].values,
    hovertemplate=(
        "<b>%{x}</b><br>"
        "Finish: %{customdata[0]}<br>"
        "Z-Score: %{y:.3f}<br>"
        "FIS Pts: %{customdata[1]:.1f}<br>"
        "Pts Gained: %{customdata[2]:+.1f}<extra></extra>"
    ),
))
fig_dist.add_hline(y=0, line_dash="dot", line_color="gray")
fig_dist.update_layout(
    height=max(320, len(race_df) * 14),
    margin=dict(t=20, b=20),
    yaxis_title="Z-Score vs Field",
    xaxis=dict(tickangle=-45),
    showlegend=False,
)
st.plotly_chart(fig_dist, use_container_width=True)

st.markdown("---")

# ---------------------------------------------------------------------------
# Bib vs Finish — did athletes outperform their seed?
# ---------------------------------------------------------------------------

if not raw_df.empty and raw_df["bib"].notna().any():
    st.subheader("Bib vs Finish Position")
    st.caption(
        "X-axis = start bib number (lower bib = stronger seed). "
        "Y-axis = final finish position (lower = better). "
        "Dots above the diagonal line beat their seeding; dots below underperformed it. "
        "Blue = outperformed seed, red = underperformed."
    )

    bib_plot = raw_df.copy()
    # Keep only athletes with numeric bib and numeric rank
    bib_plot = bib_plot[
        bib_plot["bib"].notna() &
        bib_plot["rank"].notna() &
        bib_plot["rank"].astype(str).str.match(r"^\d+$")
    ].copy()
    bib_plot["bib"]  = pd.to_numeric(bib_plot["bib"],  errors="coerce")
    bib_plot["rank"] = pd.to_numeric(bib_plot["rank"], errors="coerce")
    bib_plot = bib_plot.dropna(subset=["bib", "rank"])

    if not bib_plot.empty:
        bib_plot["beat_seed"] = bib_plot["rank"] < bib_plot["bib"]
        bib_plot["color"]     = bib_plot["beat_seed"].map({True: "#1f77b4", False: "#d62728"})
        bib_plot["label"]     = bib_plot["beat_seed"].map({True: "Beat seed", False: "Missed seed"})

        max_val = max(bib_plot["bib"].max(), bib_plot["rank"].max()) + 1

        fig_bib = go.Figure()
        # Diagonal reference line
        fig_bib.add_trace(go.Scatter(
            x=[1, max_val], y=[1, max_val],
            mode="lines",
            line=dict(color="gray", dash="dot", width=1.5),
            showlegend=False,
            hoverinfo="skip",
        ))
        # Scatter points
        for beat, grp in bib_plot.groupby("beat_seed"):
            fig_bib.add_trace(go.Scatter(
                x=grp["bib"],
                y=grp["rank"],
                mode="markers",
                name="Beat seed" if beat else "Missed seed",
                marker=dict(
                    color="#1f77b4" if beat else "#d62728",
                    size=10,
                    opacity=0.85,
                    line=dict(width=0.5, color="white"),
                ),
                text=grp["name"],
                customdata=grp[["run1", "run2", "final_time"]].values,
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Bib: %{x}  →  Finish: %{y}<br>"
                    "Run 1: %{customdata[0]}<br>"
                    "Run 2: %{customdata[1]}<br>"
                    "Final: %{customdata[2]}<extra></extra>"
                ),
            ))

        fig_bib.update_layout(
            height=420,
            margin=dict(t=20, b=20),
            xaxis_title="Bib Number (seed)",
            yaxis_title="Finish Position",
            xaxis=dict(range=[0, max_val]),
            yaxis=dict(range=[max_val, 0]),  # invert so 1st place is at top
            legend=dict(orientation="h", y=1.05, x=0),
        )
        st.plotly_chart(fig_bib, use_container_width=True)
    else:
        st.info("Bib / rank data not available for this race.")

st.markdown("---")

# ---------------------------------------------------------------------------
# Full Results Table
# ---------------------------------------------------------------------------

st.subheader("Full Results")
st.caption(
    "Complete field sorted by performance (best to worst). "
    "Bib = start number. Run 1 / Run 2 = split times. "
    "FIS Pts = the athlete's FIS points in this race. "
    "Pts Gained = points gained vs the field average (positive = above average). "
    "Z-Score = performance in standard deviations vs the field."
)

display = race_df[["finish", "name", "fis_points", "points_gained", "race_z_score"]].copy()
display["fis_points"]    = display["fis_points"].round(1)
display["points_gained"] = display["points_gained"].round(1)
display["race_z_score"]  = display["race_z_score"].round(3)

# Merge raw columns (bib, country, run times) if available
if not raw_df.empty:
    raw_cols = raw_df[["name", "bib", "country", "run1", "run2", "final_time"]].copy()
    display = display.merge(raw_cols, on="name", how="left")
    # Reorder: finish, bib, name, country, run1, run2, final_time, fis_points, pts_gained, z-score
    has_run2 = display["run2"].notna().any() and (display["run2"].str.strip() != "").any()
    if has_run2:
        col_order = ["finish", "bib", "name", "country", "run1", "run2", "final_time",
                     "fis_points", "points_gained", "race_z_score"]
    else:
        col_order = ["finish", "bib", "name", "country", "run1", "final_time",
                     "fis_points", "points_gained", "race_z_score"]
    col_order = [c for c in col_order if c in display.columns]
    display = display[col_order]
    rename_map = {
        "finish": "Finish", "bib": "Bib", "name": "Name", "country": "Country",
        "run1": "Run 1", "run2": "Run 2", "final_time": "Final Time",
        "fis_points": "FIS Pts", "points_gained": "Pts Gained", "race_z_score": "Z-Score",
    }
    display.columns = [rename_map.get(c, c) for c in display.columns]
else:
    display.columns = ["Finish", "Name", "FIS Pts", "Pts Gained", "Z-Score"]

st.dataframe(display, use_container_width=True, hide_index=True, height=500)

