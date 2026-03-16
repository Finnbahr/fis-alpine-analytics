"""
Alpine Analytics — Recruiting Board

Ranks athletes within an age cohort using a multi-factor Scout Rating.
Philosophy: field-normalised performance, consistency, reliability, and
trajectory together reveal development potential better than raw FIS
points alone. Designed for college programmes and independent teams
evaluating athletes 21 and under.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from database import query


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_YEAR = 2026

COHORTS = {
    "U21 & under  (born 2005+)": (2005, CURRENT_YEAR),
    "U18  (born 2008+)":         (2008, CURRENT_YEAR),
    "U21  (born 2005–2007)":     (2005, 2007),
    "U23  (born 2003–2004)":     (2003, 2004),
    "Young Senior  (born 1998–2002)": (1998, 2002),
    "Custom range":               None,
}

RACE_LEVEL_GROUPS = {
    "All levels": None,
    "World Cup": [
        "World Cup", "World Cup Speed Event", "Audi FIS Ski World Cup",
        "Olympic Winter Games", "World Championships",
    ],
    "Europa Cup": [
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

# Score weights (must sum to 1.0)
W_FORM        = 0.40
W_CONSISTENCY = 0.25
W_RELIABILITY = 0.20
W_TRAJECTORY  = 0.15


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_recruiting_data() -> pd.DataFrame:
    """
    Pull athlete recruiting data: field-normalised z-score performance,
    consistency, DNF reliability, current trajectory, and weather versatility.
    One row per athlete × discipline × race_type.
    """
    return query("""
        WITH
        yob_country AS (
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
        ),
        -- Most recent hot-streak state per athlete × discipline
        latest_streak AS (
            SELECT DISTINCT ON (fis_code, discipline)
                fis_code,
                discipline,
                ewma_race_z,
                momentum_z,
                race_count AS streak_races
            FROM athlete_aggregate.hot_streak
            WHERE race_z_score IS NOT NULL
            ORDER BY fis_code, discipline, date DESC
        ),
        -- Weather versatility: std of avg_z across condition bins (≥3 bins required)
        weather_versatility AS (
            SELECT
                fis_code,
                discipline,
                STDDEV(avg_z_score)  AS weather_std,
                COUNT(*)             AS weather_bin_count,
                AVG(avg_z_score)     AS weather_mean_z,
                MIN(avg_z_score)     AS weather_worst_z
            FROM athlete_aggregate.weather_performance
            GROUP BY fis_code, discipline
            HAVING COUNT(*) >= 3
        ),
        -- Discipline versatility: how many disciplines each athlete competes in
        disc_versatility AS (
            SELECT fis_code::text AS fis_code, COUNT(DISTINCT discipline) AS n_disciplines
            FROM athlete_aggregate.performance_consistency_career
            WHERE races >= 3
            GROUP BY fis_code
        )
        SELECT
            pc.fis_code,
            pc.name,
            pc.discipline,
            pc.race_type,
            pc.races                                        AS race_count,
            yc.yob,
            yc.country,
            gm.sex,
            -- Field-normalised performance (z-score: positive = above field avg)
            ROUND(pc.mean_race_z_score::numeric, 3)        AS mean_z,
            ROUND(pc.std_race_z_score::numeric, 3)         AS std_z,
            ROUND(pc.cv_race_z::numeric, 3)                AS cv_z,
            -- DNF / reliability
            ROUND((pc.dnf_rate * 100)::numeric, 1)         AS dnf_pct,
            pc.max_dnf_streak                              AS max_dnf_streak,
            -- Bounce-back
            ROUND(pc.bounce_back_z_score::numeric, 3)      AS bounce_back_z,
            ROUND((pc.re_dnf_rate * 100)::numeric, 1)      AS re_dnf_pct,
            -- Trajectory / current form (from hot streak)
            ROUND(ls.ewma_race_z::numeric, 3)              AS current_form_z,
            ROUND(ls.momentum_z::numeric, 3)               AS momentum_z,
            -- Weather versatility
            ROUND(wv.weather_std::numeric, 3)              AS weather_std,
            wv.weather_bin_count,
            ROUND(wv.weather_worst_z::numeric, 3)          AS weather_worst_z,
            -- Versatility
            dv.n_disciplines
        FROM athlete_aggregate.performance_consistency_career pc
        LEFT JOIN yob_country     yc ON yc.fis_code = pc.fis_code
        LEFT JOIN gender_map      gm ON gm.fis_code = pc.fis_code
        LEFT JOIN latest_streak   ls ON ls.fis_code = pc.fis_code
                                     AND ls.discipline = pc.discipline
        LEFT JOIN weather_versatility wv ON wv.fis_code = pc.fis_code
                                         AND wv.discipline = pc.discipline
        LEFT JOIN disc_versatility dv ON dv.fis_code = pc.fis_code
        WHERE pc.races >= 3
          AND pc.mean_race_z_score IS NOT NULL
    """)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Percentile rank scaled 0–100. ascending=True means higher raw = higher score."""
    n = len(series)
    if n <= 1:
        return pd.Series([100.0] * n, index=series.index)
    ranked = series.rank(method="average", ascending=ascending, na_option="bottom")
    return ((ranked - 1) / (n - 1) * 100).round(1)


def compute_scout_rating(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-component 0–100 scores and a weighted composite Scout Rating.
    All scores are relative to the filtered cohort.
    """
    df = df.copy()

    # 1. Form: higher mean_z = better
    df["score_form"] = _pct_rank(df["mean_z"], ascending=True)

    # 2. Consistency: lower cv_z = better (use std_z as fallback)
    cv = df["cv_z"].where(df["cv_z"].notna(), df["std_z"])
    df["score_consistency"] = _pct_rank(cv, ascending=False)

    # 3. Reliability: lower dnf_pct = better
    df["score_reliability"] = _pct_rank(df["dnf_pct"], ascending=False)

    # 4. Trajectory: higher momentum_z = better (improving athlete)
    traj = df["momentum_z"].fillna(0.0)
    df["score_trajectory"] = _pct_rank(traj, ascending=True)

    # Composite
    df["scout_rating"] = (
        W_FORM        * df["score_form"]
        + W_CONSISTENCY * df["score_consistency"]
        + W_RELIABILITY * df["score_reliability"]
        + W_TRAJECTORY  * df["score_trajectory"]
    ).round(1)

    return df


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Recruiting Board — Alpine Analytics",
    layout="wide",
)

st.title("Recruiting Board")

st.markdown(
    "Field-normalised performance, consistency, reliability, and trajectory — "
    "four dimensions that together reveal development potential far better than "
    "raw FIS points alone. Scores are relative to whoever is currently in the "
    "filtered pool; changing filters recalculates all ratings."
)

with st.expander("How Scout Rating is calculated", expanded=False):
    st.markdown(f"""
**Scout Rating** is a 0–100 composite. Each component is percentile-ranked within
the current filtered pool and then weighted:

| Component | Weight | Signal | Direction |
|---|---|---|---|
| **Form** | {W_FORM:.0%} | Mean z-score vs field across all races | Higher = above-average finisher |
| **Consistency** | {W_CONSISTENCY:.0%} | CV of z-scores (spread relative to own mean) | Lower = more reliable |
| **Reliability** | {W_RELIABILITY:.0%} | DNF / DSQ / DNS rate | Lower = finishes races |
| **Trajectory** | {W_TRAJECTORY:.0%} | Momentum z (recent vs career baseline) | Higher = currently improving |

**Z-score explained:** A z-score of 0 means the athlete finished exactly at field average
for every race. +0.5 is comfortably above average; +1.0+ is top-tier. Unlike FIS points,
z-scores adjust for field strength — winning a FIS race against 80 athletes scores similarly
to a strong finish in a World Cup.

**Weather versatility** is shown as a separate indicator and does not affect Scout Rating.
It requires at least 3 condition bins of history to be meaningful.
    """)


# ---------------------------------------------------------------------------
# Data load
# ---------------------------------------------------------------------------

with st.spinner("Loading athlete data..."):
    df_all = load_recruiting_data()

if df_all.empty:
    st.error("No data available.")
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

gender_choice = st.sidebar.radio("Gender", ["Men's", "Women's"], horizontal=True)

disc_choice = st.sidebar.selectbox("Discipline", DISCIPLINES)

level_choice = st.sidebar.selectbox("Race Level", list(RACE_LEVEL_GROUPS.keys()))

cohort_choice = st.sidebar.selectbox(
    "Age Cohort", list(COHORTS.keys()), index=0
)

if cohort_choice == "Custom range":
    col_a, col_b = st.sidebar.columns(2)
    yob_min = col_a.number_input("Born from", min_value=1970, max_value=CURRENT_YEAR, value=2003)
    yob_max = col_b.number_input("Born to",   min_value=1970, max_value=CURRENT_YEAR, value=2007)
    yob_range = (int(yob_min), int(yob_max))
else:
    yob_range = COHORTS[cohort_choice]

min_races = st.sidebar.slider("Minimum races", min_value=3, max_value=30, value=5)

country_search = st.sidebar.text_input(
    "Filter by country (e.g. USA, AUT)",
    help="Leave blank to show all countries.",
).strip().upper()


# ---------------------------------------------------------------------------
# Apply filters
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

if country_search:
    df = df[df["country"].str.upper().str.contains(country_search, na=False)]

if df.empty:
    st.info("No athletes match the current filters. Try relaxing the requirements.")
    st.stop()

# When "All" disciplines selected: keep the athlete's most-raced discipline row.
# When a specific discipline is selected: all rows are already filtered.
if disc_choice == "All":
    df = df.sort_values("race_count", ascending=False).drop_duplicates("fis_code").copy()


# ---------------------------------------------------------------------------
# Score and rank
# ---------------------------------------------------------------------------

df = compute_scout_rating(df)
df = df.sort_values("scout_rating", ascending=False).reset_index(drop=True)
df.index += 1
df.index.name = "Rank"


# ---------------------------------------------------------------------------
# Summary metrics row
# ---------------------------------------------------------------------------

n_athletes  = len(df)
top_prospect = df.iloc[0]["name"] if n_athletes > 0 else "—"
med_form     = df["mean_z"].median()
med_cv       = df["cv_z"].median()
med_dnf      = df["dnf_pct"].median()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Athletes ranked", n_athletes)
c2.metric("Median form (z)", f"{med_form:+.2f}")
c3.metric("Median consistency (CV)", f"{med_cv:.2f}")
c4.metric("Median DNF rate", f"{med_dnf:.1f}%")

st.divider()


# ---------------------------------------------------------------------------
# Leaderboard table
# ---------------------------------------------------------------------------

st.subheader("Leaderboard")

def _weather_label(row) -> str:
    if pd.isna(row["weather_std"]) or pd.isna(row["weather_bin_count"]):
        return "Limited data"
    std = row["weather_std"]
    if std < 0.20:
        return "Excellent"
    elif std < 0.35:
        return "Good"
    elif std < 0.55:
        return "Moderate"
    else:
        return "Variable"

df["weather_versatility"] = df.apply(_weather_label, axis=1)

display_cols = {
    "name":               "Name",
    "country":            "Country",
    "yob":                "YOB",
    "discipline":         "Discipline",
    "race_count":         "Races",
    "mean_z":             "Form (z)",
    "score_form":         "Form Score",
    "score_consistency":  "Consistency",
    "score_reliability":  "Reliability",
    "score_trajectory":   "Trajectory",
    "weather_versatility":"All-Conditions",
    "dnf_pct":            "DNF%",
    "n_disciplines":      "Disciplines",
    "scout_rating":       "Scout Rating",
}

table = df[list(display_cols.keys())].rename(columns=display_cols)
table["YOB"]         = table["YOB"].astype("Int64")
table["Disciplines"] = table["Disciplines"].astype("Int64")

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
        "Form Score": st.column_config.ProgressColumn(
            "Form Score",
            format="%.0f",
            min_value=0,
            max_value=100,
            help="Percentile rank of field-normalised mean z-score within this cohort",
        ),
        "Consistency": st.column_config.ProgressColumn(
            "Consistency",
            format="%.0f",
            min_value=0,
            max_value=100,
            help="Percentile rank of inverse CV — how reliably the athlete hits their level",
        ),
        "Reliability": st.column_config.ProgressColumn(
            "Reliability",
            format="%.0f",
            min_value=0,
            max_value=100,
            help="Percentile rank of inverse DNF rate — finishes races",
        ),
        "Trajectory": st.column_config.ProgressColumn(
            "Trajectory",
            format="%.0f",
            min_value=0,
            max_value=100,
            help="Percentile rank of momentum z — currently improving vs declining",
        ),
        "Form (z)":        st.column_config.NumberColumn("Form (z)",   format="%+.3f", help="Mean z-score vs field. 0 = field average; +0.5 = comfortably above average"),
        "DNF%":            st.column_config.NumberColumn("DNF%",       format="%.1f%%"),
        "All-Conditions":  st.column_config.TextColumn("All-Conditions", help="Consistency of performance across different weather conditions"),
        "Races":           st.column_config.NumberColumn("Races"),
        "Disciplines":     st.column_config.NumberColumn("Disciplines", help="Number of disciplines with ≥3 starts"),
    },
    height=min(650, 55 + 35 * len(table)),
)


# ---------------------------------------------------------------------------
# Charts — Form vs Consistency scatter + Athlete radar
# ---------------------------------------------------------------------------

st.divider()
chart_col, radar_col = st.columns([3, 2])

with chart_col:
    st.subheader("Form vs Consistency")
    st.caption(
        "Top-right = fast and consistent (target zone). "
        "Bubble size = race count. Color = Scout Rating."
    )

    scatter_df = df.copy()
    scatter_df["consistency_pct"] = scatter_df["score_consistency"]

    fig = px.scatter(
        scatter_df,
        x="mean_z",
        y="score_consistency",
        size="race_count",
        color="scout_rating",
        hover_name="name",
        hover_data={
            "country":       True,
            "yob":           True,
            "race_count":    True,
            "mean_z":        ":.3f",
            "dnf_pct":       ":.1f",
            "scout_rating":  ":.1f",
        },
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
        size_max=28,
        labels={
            "mean_z":           "Form — Mean z-score vs field",
            "score_consistency":"Consistency Score (0–100)",
            "scout_rating":     "Scout Rating",
            "race_count":       "Races",
        },
        template="plotly_white",
    )
    fig.add_vline(x=0, line_dash="dot", line_color="gray", opacity=0.4)
    fig.update_traces(marker_opacity=0.78)
    fig.update_layout(
        height=440,
        coloraxis_colorbar=dict(title="Scout Rating"),
        font=dict(size=12),
        margin=dict(l=50, r=20, t=20, b=50),
    )
    st.plotly_chart(fig, use_container_width=True)


with radar_col:
    st.subheader("Athlete Spotlight")

    athlete_names = df["name"].tolist()
    sel_name = st.selectbox(
        "Select athlete",
        athlete_names,
        index=0,
        label_visibility="collapsed",
    )

    sel = df[df["name"] == sel_name].iloc[0]

    # Radar dimensions
    categories = ["Form", "Consistency", "Reliability", "Trajectory"]
    values     = [
        float(sel["score_form"]),
        float(sel["score_consistency"]),
        float(sel["score_reliability"]),
        float(sel["score_trajectory"]),
    ]
    # Close the polygon
    categories_closed = categories + [categories[0]]
    values_closed     = values + [values[0]]

    fig_r = go.Figure()
    fig_r.add_trace(go.Scatterpolar(
        r     = values_closed,
        theta = categories_closed,
        fill  = "toself",
        fillcolor = "rgba(26, 58, 107, 0.20)",
        line  = dict(color="#1a3a6b", width=2),
        name  = sel_name.split()[-1],
    ))
    fig_r.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 100],
                tickvals=[25, 50, 75, 100],
                tickfont=dict(size=10),
                gridcolor="#ddd",
            ),
            angularaxis=dict(tickfont=dict(size=13)),
        ),
        showlegend=False,
        height=340,
        margin=dict(l=40, r=40, t=30, b=30),
        paper_bgcolor="white",
    )
    st.plotly_chart(fig_r, use_container_width=True)

    # Key stats below radar
    age = CURRENT_YEAR - int(sel["yob"]) if pd.notna(sel["yob"]) else "—"
    st.markdown(f"""
**{sel_name}**
{sel.get('country','') or ''}  ·  Born {int(sel['yob']) if pd.notna(sel['yob']) else '—'} (age {age})  ·  {sel['discipline']}

| Metric | Value |
|---|---|
| Scout Rating | **{sel['scout_rating']:.1f}** / 100 |
| Form (mean z) | **{sel['mean_z']:+.3f}** |
| Consistency (CV) | **{sel['cv_z']:.3f}** |
| DNF rate | **{sel['dnf_pct']:.1f}%** |
| Trajectory | **{"Improving" if (sel['momentum_z'] or 0) > 0.05 else "Declining" if (sel['momentum_z'] or 0) < -0.05 else "Stable"}** |
| All-conditions | **{sel['weather_versatility']}** |
| Disciplines raced | **{int(sel['n_disciplines']) if pd.notna(sel['n_disciplines']) else 1}** |
| Race count | **{int(sel['race_count'])}** |
    """)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

st.divider()
csv_cols = [
    "name", "country", "yob", "discipline", "race_type", "race_count",
    "mean_z", "cv_z", "dnf_pct", "momentum_z",
    "score_form", "score_consistency", "score_reliability", "score_trajectory",
    "weather_versatility", "n_disciplines", "scout_rating",
]
csv_out = df.reset_index()[csv_cols].to_csv(index=False)
st.download_button(
    "Download board as CSV",
    data=csv_out,
    file_name=f"recruiting_board_{gender_choice.replace(' ','_')}_{disc_choice}.csv",
    mime="text/csv",
)
