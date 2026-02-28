"""
Alpine Analytics — Recruiting Board

Ranks athletes within an age cohort using a consistency-weighted composite score.
Philosophy: a reliable 40-point skier is a safer development bet than one who peaks
at 38 once but surrounds it with 55s.

Scout Rating formula:
  raw_score = mean_fis + 0.75 * std_fis + (dnf_rate * 50)
  Scout Rating 0–100: 100 = best in cohort
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
    ranks = df["raw_score"].rank(method="min", ascending=True)
    df["scout_rating"] = ((1 - (ranks - 1) / (n - 1)) * 100).round(1)
    return df


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Recruiting Board — Alpine Analytics",
    layout="wide",
)

# Under-construction blur overlay
st.markdown("""
<style>
.uc-overlay {
    position: fixed;
    inset: 0;
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    background: rgba(255,255,255,0.25);
    z-index: 99999;
    display: flex;
    align-items: center;
    justify-content: center;
}
.uc-card {
    background: #ffffff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    padding: 2.5rem 3.5rem;
    text-align: center;
    box-shadow: 0 8px 32px rgba(0,0,0,0.12);
    max-width: 460px;
}
.uc-card h2 { margin: 0 0 0.75rem 0; font-size: 1.5rem; color: #1a1a1a; font-weight: 600; }
.uc-card p  { margin: 0; color: #555; font-size: 0.92rem; line-height: 1.6; }
</style>
<div class="uc-overlay">
    <div class="uc-card">
        <h2>Under Construction</h2>
        <p>Scoring model and data validation are in progress.<br>Check back soon.</p>
    </div>
</div>
""", unsafe_allow_html=True)

st.title("Recruiting Board")

st.markdown(
    "Raw FIS points tell you how an athlete finished on their best day. "
    "They don't tell you how reliably they hit that level. "
    "This board surfaces consistent performers — athletes who post a stable "
    "number race after race rank above those with one standout result surrounded "
    "by blown runs and DNFs. Ranking is relative to the filtered cohort; "
    "switch the year filter to compare athletes within a single birth year class."
)

# Load data
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


# ---------------------------------------------------------------------------
# Apply filters (gender, discipline, level, cohort, min races)
# ---------------------------------------------------------------------------

df = df_all.copy()

df = df[df["sex"] == gender_choice]

if disc_choice != "All":
    df = df[df["discipline"] == disc_choice]

race_types = RACE_LEVEL_GROUPS[level_choice]
if race_types is not None:
    df = df[df["race_type"].isin(race_types)]

if yob_range is not None:
    df = df[df["yob"].between(yob_range[0], yob_range[1])]

df = df[df["race_count"] >= min_races]

if df.empty:
    st.info("No athletes match the current filters. Try relaxing the requirements.")
    st.stop()

# Keep one row per athlete — the discipline/level combo with most races
df = df.sort_values("race_count", ascending=False).drop_duplicates("fis_code")


# ---------------------------------------------------------------------------
# Year split filter (above the table, inline)
# ---------------------------------------------------------------------------

available_years = sorted(df["yob"].dropna().astype(int).unique())
year_options = ["All years"] + [str(y) for y in available_years]

year_col, _ = st.columns([2, 5])
with year_col:
    year_choice = st.selectbox(
        "Rank within birth year",
        year_options,
        help="Narrows the ranking pool to a single birth year. Scout Ratings recalculate within that group.",
    )

if year_choice != "All years":
    df = df[df["yob"] == int(year_choice)]

if df.empty:
    st.info("No athletes for this birth year with the current filters.")
    st.stop()


# ---------------------------------------------------------------------------
# Score and rank
# ---------------------------------------------------------------------------

df = compute_scout_rating(df)
df = df.sort_values("scout_rating", ascending=False).reset_index(drop=True)
df.index += 1
df.index.name = "Rank"


# ---------------------------------------------------------------------------
# Cohort summary
# ---------------------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
cohort_label = year_choice if year_choice != "All years" else cohort_choice.split("(")[0].strip()
c1.metric("Athletes ranked", len(df))
c2.metric("Median mean FIS", f"{df['mean_fis'].median():.1f}")
c3.metric("Median std FIS", f"{df['std_fis'].median():.1f}")
c4.metric("Median DNF%", f"{df['dnf_pct'].median():.1f}%")

st.divider()


# ---------------------------------------------------------------------------
# Ranking explanation — above the table
# ---------------------------------------------------------------------------

with st.expander("How Scout Rating is calculated", expanded=False):
    st.markdown("""
**Scout Rating** is a 0–100 composite score computed fresh for whoever is currently
in the ranked pool. Adding or removing filters changes individual scores because
ratings are relative — 100 always goes to the best athlete in the current group.

| Component | Weight | Why it matters |
|---|---|---|
| Mean FIS points | 1.0× | Core result quality — lower is faster |
| Std FIS points | 0.75× | Consistency penalty — high variance is punished |
| DNF rate | ×50 pts | Reliability — finishing races is non-negotiable |

**Formula:** `raw_score = mean_fis + 0.75 × std_fis + dnf_rate × 50`

Scores are then ranked within the pool and scaled to 0–100.

**Example:** An athlete averaging 40 FIS pts with a std of 3 scores a raw of **42.3**.
An athlete averaging 38 but with a std of 15 scores a raw of **49.25** — ranked lower
despite the better mean, because you can't rely on them to hit that level again.
    """)


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
# Consistency scatter
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Consistency Map")
st.caption(
    "Bottom-left = fast and consistent (target zone). "
    "Bubble size = race count. Color = Scout Rating."
)

fig = px.scatter(
    df,
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
        "mean_fis": "Mean FIS Points",
        "std_fis": "Std FIS Points",
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
