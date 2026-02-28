"""
Alpine Analytics — Recruiting Board

Ranks athletes within an age cohort using a consistency-weighted composite score.
Philosophy: a reliable 40-point skier is more valuable than one who peaks at 38
but averages 52 with high variance.

Composite (Scout Rating):
  raw_score = mean_fis + 0.75 * std_fis + (dnf_rate * 50)
  Scout Rating 1–100: 100 = best in cohort, 0 = worst
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from database import query


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_YEAR = 2026

COHORTS = {
    "U18  (born 2008–2010)": (2008, 2010),
    "U21  (born 2005–2007)": (2005, 2007),
    "U23  (born 2003–2004)": (2003, 2004),
    "Young Senior  (born 1998–2002)": (1998, 2002),
    "Custom range": None,
}

# Map display label → list of raw race_type strings
RACE_LEVEL_GROUPS = {
    "All levels": None,
    "World Cup": [
        "World Cup", "World Cup Speed Event", "Audi FIS Ski World Cup",
        "Olympic Winter Games", "World Championships",
    ],
    "European Cup": [
        "European Cup", "European Cup Speed Event",
        "CIT", "CIT Arnold Lunn World Cup",
    ],
    "Continental Cup": [
        "Nor-Am Cup", "South American Cup",
        "Australian New Zealand Cup", "Far East Cup", "Asian Winter Games",
    ],
    "FIS / Junior": [
        "FIS", "FIS Junior World Ski Championships", "FIS Qualification",
        "National Junior Championships", "National Junior Race",
        "National Championships", "Entry League FIS",
    ],
}

DISCIPLINES = ["All", "Slalom", "Giant Slalom", "Super G", "Downhill", "Alpine Combined"]


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_recruiting_data() -> pd.DataFrame:
    """
    Pull all athlete career stats needed for the recruiting board.
    Attaches YOB, country (modal value from fis_results), and gender
    (derived from the sex of races each athlete competed in).
    """
    return query("""
        WITH yob_country AS (
            SELECT DISTINCT ON (fis_code)
                fis_code::text AS fis_code,
                yob,
                country
            FROM (
                SELECT fis_code, yob, country, COUNT(*) AS cnt
                FROM raw.fis_results
                WHERE yob IS NOT NULL
                  AND country IS NOT NULL AND country <> ''
                GROUP BY fis_code, yob, country
            ) t
            ORDER BY fis_code, cnt DESC
        ),
        gender_map AS (
            SELECT DISTINCT ON (fis_code)
                fis_code, sex
            FROM (
                SELECT fr.fis_code::text AS fis_code, rd.sex, COUNT(*) AS cnt
                FROM raw.fis_results fr
                JOIN raw.race_details rd ON rd.race_id = fr.race_id
                WHERE rd.sex IS NOT NULL
                GROUP BY fr.fis_code, rd.sex
            ) g
            ORDER BY fis_code, cnt DESC
        )
        SELECT
            b.fis_code,
            b.name,
            b.discipline,
            b.race_type,
            b.race_count,
            ROUND(b.mean_fis_points::numeric, 1)  AS mean_fis,
            ROUND(b.std_fis_points::numeric, 1)   AS std_fis,
            ROUND((b.dnf_rate * 100)::numeric, 1) AS dnf_pct,
            yc.yob,
            yc.country,
            gm.sex
        FROM athlete_aggregate.basic_athlete_info_career b
        LEFT JOIN yob_country yc ON yc.fis_code = b.fis_code
        LEFT JOIN gender_map  gm ON gm.fis_code = b.fis_code
        WHERE b.race_count >= 3
          AND b.mean_fis_points IS NOT NULL
          AND b.std_fis_points  IS NOT NULL
    """)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_scout_rating(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add `raw_score` and `scout_rating` (1–100) columns to df.
    Lower raw_score = more reliable = higher scout rating.
    """
    df = df.copy()
    df["raw_score"] = (
        df["mean_fis"]
        + 0.75 * df["std_fis"]
        + (df["dnf_pct"] / 100) * 50
    )
    n = len(df)
    if n <= 1:
        df["scout_rating"] = 100.0
        return df
    # Rank ascending (best = rank 1 = lowest raw_score)
    ranks = df["raw_score"].rank(method="min", ascending=True)
    df["scout_rating"] = ((1 - (ranks - 1) / (n - 1)) * 100).round(1)
    return df


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Recruiting Board — Alpine Analytics",
    layout="wide",
)

st.title("Recruiting Board")
st.caption(
    "Ranks athletes by a consistency-weighted composite. "
    "A skier who logs 40 FIS points reliably scores higher than one who peaks "
    "at 38 but falls off to 55+ regularly."
)

# Load data once
with st.spinner("Loading athlete data..."):
    df_all = load_recruiting_data()

if df_all.empty:
    st.error("No data available.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

gender_choice = st.sidebar.radio(
    "Gender",
    options=["Men's", "Women's"],
    horizontal=True,
)

disc_choice = st.sidebar.selectbox("Discipline", DISCIPLINES)

level_choice = st.sidebar.selectbox("Race Level", list(RACE_LEVEL_GROUPS.keys()))

cohort_choice = st.sidebar.selectbox("Age Cohort", list(COHORTS.keys()))

if cohort_choice == "Custom range":
    col_a, col_b = st.sidebar.columns(2)
    yob_min = col_a.number_input("Born from", min_value=1970, max_value=CURRENT_YEAR, value=2000)
    yob_max = col_b.number_input("Born to",   min_value=1970, max_value=CURRENT_YEAR, value=2008)
    yob_range = (int(yob_min), int(yob_max))
else:
    yob_range = COHORTS[cohort_choice]

min_races = st.sidebar.slider("Minimum races", min_value=3, max_value=30, value=5)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Scout Rating formula**\n\n"
    "```\nraw = mean_fis\n"
    "    + 0.75 × std_fis\n"
    "    + dnf_rate × 50\n```\n"
    "Lower raw score → higher rating (1–100). "
    "Std penalty rewards athletes who perform the same level every time."
)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------

df = df_all.copy()

# Gender
df = df[df["sex"] == gender_choice]

# Discipline
if disc_choice != "All":
    df = df[df["discipline"] == disc_choice]

# Race level
race_types = RACE_LEVEL_GROUPS[level_choice]
if race_types is not None:
    df = df[df["race_type"].isin(race_types)]

# YOB range
if yob_range is not None:
    df = df[df["yob"].between(yob_range[0], yob_range[1])]

# Min races
df = df[df["race_count"] >= min_races]

if df.empty:
    st.info("No athletes match the current filters. Try relaxing the requirements.")
    st.stop()

# When a single athlete has stats for multiple disciplines or race types,
# they appear multiple times. For the recruiting board we want one row per
# athlete (the highest-level discipline row, or the one with most races).
# De-dup: keep the row with the most races per fis_code.
df = df.sort_values("race_count", ascending=False).drop_duplicates("fis_code")

# Compute scores within this filtered cohort
df = compute_scout_rating(df)
df = df.sort_values("scout_rating", ascending=False).reset_index(drop=True)
df.index += 1  # 1-based rank
df.index.name = "Rank"

# ---------------------------------------------------------------------------
# Cohort summary
# ---------------------------------------------------------------------------

col1, col2, col3, col4 = st.columns(4)
col1.metric("Athletes in cohort", len(df))
col2.metric("Median mean FIS", f"{df['mean_fis'].median():.1f}")
col3.metric("Median std FIS", f"{df['std_fis'].median():.1f}")
col4.metric("Median DNF%", f"{df['dnf_pct'].median():.1f}%")

st.divider()

# ---------------------------------------------------------------------------
# Leaderboard table
# ---------------------------------------------------------------------------

st.subheader("Leaderboard")

display_cols = {
    "name":         "Name",
    "country":      "Country",
    "yob":          "YOB",
    "discipline":   "Discipline",
    "race_type":    "Race Level",
    "race_count":   "Races",
    "mean_fis":     "Mean FIS",
    "std_fis":      "Std FIS",
    "dnf_pct":      "DNF%",
    "scout_rating": "Scout Rating",
}

table = df[list(display_cols.keys())].rename(columns=display_cols)
table["YOB"] = table["YOB"].astype("Int64")

st.dataframe(
    table,
    use_container_width=True,
    column_config={
        "Scout Rating": st.column_config.ProgressColumn(
            "Scout Rating",
            format="%.1f",
            min_value=0,
            max_value=100,
        ),
        "Mean FIS": st.column_config.NumberColumn("Mean FIS", format="%.1f"),
        "Std FIS":  st.column_config.NumberColumn("Std FIS",  format="%.1f"),
        "DNF%":     st.column_config.NumberColumn("DNF%",     format="%.1f%%"),
    },
    height=min(600, 40 + 35 * len(table)),
)

# ---------------------------------------------------------------------------
# Scatter: consistency map
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Consistency Map")
st.caption(
    "Each bubble is an athlete. Left = lower mean FIS (faster). "
    "Down = lower variability (more consistent). "
    "Bubble size = race count. Color = Scout Rating."
)

plot_df = df.copy()
plot_df["Age"] = CURRENT_YEAR - plot_df["yob"].fillna(0).astype(int)

fig = px.scatter(
    plot_df,
    x="mean_fis",
    y="std_fis",
    size="race_count",
    color="scout_rating",
    hover_name="name",
    hover_data={
        "country": True,
        "yob": True,
        "race_count": True,
        "mean_fis": ":.1f",
        "std_fis": ":.1f",
        "dnf_pct": ":.1f",
        "scout_rating": ":.1f",
    },
    color_continuous_scale="RdYlGn",
    range_color=[0, 100],
    size_max=30,
    labels={
        "mean_fis": "Mean FIS Points (lower = faster)",
        "std_fis": "Std FIS Points (lower = more consistent)",
        "scout_rating": "Scout Rating",
        "race_count": "Races",
    },
    template="plotly_white",
)
fig.update_traces(marker_opacity=0.75)
fig.update_layout(
    height=500,
    coloraxis_colorbar=dict(title="Scout Rating"),
    font=dict(size=12),
)
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Bottom note
# ---------------------------------------------------------------------------

st.caption(
    "Scout Rating is relative to the current filtered cohort — it changes when "
    "filters change. Minimum races filter removes athletes with insufficient "
    "data to score reliably."
)
