"""
Alpine Analytics — Recruiting Board

Multi-factor Scout Rating for athletes 21 and under.
Uses career (rolling) stats — field-normalised performance, trend-adjusted
consistency, reliability, and trajectory. Designed for college programmes
and independent teams.
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
MAX_AGE      = 21                       # hard cap — board is for U21 only
MIN_YOB      = CURRENT_YEAR - MAX_AGE  # 2005 — oldest eligible birth year

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

# Score component weights — must sum to 1.0
W_FORM        = 0.40
W_CONSISTENCY = 0.25
W_RELIABILITY = 0.20
W_TRAJECTORY  = 0.15


# ---------------------------------------------------------------------------
# Data loader — career (rolling) stats
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_recruiting_data() -> pd.DataFrame:
    """Career stats for all athletes born 2005 or later (≤21 in 2026)."""
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
        latest_streak AS (
            SELECT DISTINCT ON (fis_code, discipline)
                fis_code,
                discipline,
                ewma_race_z,
                momentum_z
            FROM athlete_aggregate.hot_streak
            WHERE race_z_score IS NOT NULL
            ORDER BY fis_code, discipline, date DESC
        ),
        weather_versatility AS (
            SELECT
                fis_code,
                discipline,
                STDDEV(avg_z_score) AS weather_std,
                COUNT(*)            AS weather_bin_count
            FROM athlete_aggregate.weather_performance
            GROUP BY fis_code, discipline
            HAVING COUNT(*) >= 3
        ),
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
            -- Performance
            ROUND(pc.mean_race_z_score::numeric, 3)        AS mean_z,
            ROUND(pc.std_race_z_score::numeric, 3)         AS std_z,
            ROUND(pc.mean_fis::numeric, 1)                 AS mean_fis,
            ROUND(pc.min_fis_points::numeric, 1)           AS best_fis,
            -- Reliability
            ROUND((pc.dnf_rate * 100)::numeric, 1)         AS dnf_pct,
            pc.max_dnf_streak                              AS max_dnf_streak,
            -- Trajectory
            ROUND(ls.ewma_race_z::numeric, 3)              AS current_form_z,
            ROUND(ls.momentum_z::numeric, 3)               AS momentum_z,
            -- Weather
            ROUND(wv.weather_std::numeric, 3)              AS weather_std,
            wv.weather_bin_count,
            -- Versatility
            dv.n_disciplines
        FROM athlete_aggregate.performance_consistency_career pc
        LEFT JOIN yob_country       yc ON yc.fis_code = pc.fis_code
        LEFT JOIN gender_map        gm ON gm.fis_code = pc.fis_code
        LEFT JOIN latest_streak     ls ON ls.fis_code  = pc.fis_code
                                       AND ls.discipline = pc.discipline
        LEFT JOIN weather_versatility wv ON wv.fis_code  = pc.fis_code
                                          AND wv.discipline = pc.discipline
        LEFT JOIN disc_versatility  dv ON dv.fis_code = pc.fis_code
        WHERE pc.races >= 3
          AND pc.mean_race_z_score IS NOT NULL
    """)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Percentile rank 0–100. ascending=True → higher raw value = higher score."""
    n = len(series)
    if n <= 1:
        return pd.Series([100.0] * n, index=series.index)
    ranked = series.rank(method="average", ascending=ascending, na_option="bottom")
    return ((ranked - 1) / (n - 1) * 100).round(1)


def compute_scout_rating(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1. Form — career mean z-score vs field. Higher = better.
    df["score_form"] = _pct_rank(df["mean_z"], ascending=True)

    # 2. Consistency — std of z-scores, adjusted for trend.
    #    Problem: an athlete on a strong upward trajectory will have a high std
    #    simply because their early races were worse than their recent ones.
    #    Fix: give improving athletes a trend credit that reduces their effective
    #    std proportionally to momentum_z, up to 40% of observed std.
    momentum   = df["momentum_z"].fillna(0.0)
    std_median = df["std_z"].median() if df["std_z"].notna().any() else 1.0
    trend_credit = np.clip(momentum * std_median * 0.5, 0, df["std_z"].fillna(0) * 0.40)
    adj_std = (df["std_z"].fillna(std_median) - trend_credit).clip(lower=0)
    df["score_consistency"] = _pct_rank(adj_std, ascending=False)

    # 3. Reliability — DNF/DSQ/DNS rate. Lower = better.
    df["score_reliability"] = _pct_rank(df["dnf_pct"], ascending=False)

    # 4. Trajectory — momentum_z: positive = currently outperforming career baseline.
    df["score_trajectory"] = _pct_rank(df["momentum_z"].fillna(0.0), ascending=True)

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

st.set_page_config(page_title="Recruiting Board — Alpine Analytics", layout="wide")

st.title("Recruiting Board")
st.markdown(
    "Field-normalised performance, trend-adjusted consistency, reliability, and "
    "trajectory — four dimensions that together reveal development potential. "
    "All career results are used (rolling). Scores are percentile-ranked within "
    "whoever is currently in the filtered pool."
)

with st.expander("How Scout Rating is calculated", expanded=False):
    st.markdown(f"""
**Scout Rating** is a 0–100 composite, percentile-ranked within the current pool:

| Component | Weight | What it measures |
|---|---|---|
| **Form** | {W_FORM:.0%} | Career mean z-score vs field. Above 0 = above-average finisher relative to whoever else is in that race. |
| **Consistency** | {W_CONSISTENCY:.0%} | How reliably the athlete hits their own level. Measured as std of z-scores, **adjusted for upward trend** — an athlete who is genuinely improving is not penalised for the variance introduced by getting better. |
| **Reliability** | {W_RELIABILITY:.0%} | Career DNF/DSQ/DNS rate. Finishing races is non-negotiable for development athletes. |
| **Trajectory** | {W_TRAJECTORY:.0%} | Momentum z — how their recent results compare to their own career baseline. Positive = currently outperforming history. |

**Column guide:**

| Column | Meaning |
|---|---|
| **Form (z)** | Career mean z-score. 0 = field average; +0.3 comfortably above; +0.7 elite. |
| **Avg FIS** | Career average FIS points. Familiar reference — lower is faster. |
| **Best FIS** | Career-best (lowest ever) FIS points — their ceiling to date. |
| **Consistency** | 0–100 percentile. 90+ = very reliable; 50 = average variation; <30 = erratic. Adjusted for improving athletes. |
| **Reliability** | 0–100 percentile based on DNF rate. 90+ = almost always finishes; <40 = frequent DNFs. |
| **Trajectory** | 0–100 percentile based on recent vs career momentum. 75+ = clearly on the rise. |
| **All-Conditions** | How consistently the athlete performs across different weather bins (temperature, cloud, precipitation). Excellent = low spread across conditions; Limited = insufficient weather data. |
| **Events** | Number of FIS disciplines with 3+ career starts. Higher = more versatile. |
| **Scout Rating** | Weighted composite of all four components. 100 = best in current pool. |

**Consistency vs Trajectory — read them together:**
A high Trajectory + moderate Consistency means the athlete is improving but their early results
drag up the variance. This is a positive profile — check recency of form. A high Consistency +
flat Trajectory means a stable but plateaued athlete. A high Consistency + declining Trajectory
is a red flag.
    """)


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

with st.spinner("Loading athlete data..."):
    df_all = load_recruiting_data()

if df_all.empty:
    st.error("No data available.")
    st.stop()

# Pre-filter: only athletes ≤ 21 in current year (born 2005 or later)
df_all = df_all[df_all["yob"].notna() & (df_all["yob"] >= MIN_YOB)].copy()


# ---------------------------------------------------------------------------
# Sidebar filters
# ---------------------------------------------------------------------------

st.sidebar.header("Filters")

gender_choice = st.sidebar.radio("Gender", ["Men's", "Women's"], horizontal=True)
disc_choice   = st.sidebar.selectbox("Discipline", DISCIPLINES)
level_choice  = st.sidebar.selectbox("Race Level", list(RACE_LEVEL_GROUPS.keys()))

# Birth year range — constrained to ≤ 21
available_yobs = sorted(df_all["yob"].dropna().astype(int).unique())
yob_min_sel, yob_max_sel = st.sidebar.select_slider(
    "Birth Year",
    options=available_yobs,
    value=(min(available_yobs), max(available_yobs)),
    help=f"Only athletes born {MIN_YOB} or later (age ≤ 21 in {CURRENT_YEAR}) are shown.",
)

min_races = st.sidebar.slider("Minimum career races", min_value=3, max_value=30, value=5)

country_search = st.sidebar.text_input(
    "Filter by country (e.g. USA, AUT)",
).strip().upper()


# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------

df = df_all[df_all["sex"] == gender_choice].copy()

if disc_choice != "All":
    df = df[df["discipline"] == disc_choice]

race_types = RACE_LEVEL_GROUPS[level_choice]
if race_types is not None:
    df = df[df["race_type"].isin(race_types)]

df = df[df["yob"].between(yob_min_sel, yob_max_sel)]
df = df[df["race_count"] >= min_races]

if country_search:
    df = df[df["country"].str.upper().str.contains(country_search, na=False)]

if df.empty:
    st.info("No athletes match the current filters. Try relaxing the requirements.")
    st.stop()

# "All" disciplines: keep the athlete's most-raced discipline row
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
# Summary metrics
# ---------------------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Athletes ranked", len(df))
c2.metric("Median form (z)", f"{df['mean_z'].median():+.2f}")
c3.metric("Median avg FIS", f"{df['mean_fis'].median():.0f}")
c4.metric("Median DNF rate", f"{df['dnf_pct'].median():.1f}%")

st.divider()


# ---------------------------------------------------------------------------
# Leaderboard table
# ---------------------------------------------------------------------------

st.subheader("Leaderboard")

def _weather_label(row) -> str:
    if pd.isna(row.get("weather_std")) or pd.isna(row.get("weather_bin_count")):
        return "Limited"
    s = row["weather_std"]
    if s < 0.20:   return "Excellent"
    elif s < 0.35: return "Good"
    elif s < 0.55: return "Moderate"
    else:          return "Variable"

def _trajectory_label(v) -> str:
    if pd.isna(v) or v == 0:  return "Stable"
    if v >  0.15: return "Rising"
    if v < -0.15: return "Declining"
    return "Stable"

df["all_conditions"] = df.apply(_weather_label, axis=1)
df["age"]            = CURRENT_YEAR - df["yob"].astype(int)

display_cols = {
    "name":             "Name",
    "country":          "Country",
    "yob":              "YOB",
    "age":              "Age",
    "discipline":       "Discipline",
    "race_count":       "Career Races",
    "mean_z":           "Form (z)",
    "mean_fis":         "Avg FIS",
    "best_fis":         "Best FIS",
    "score_form":       "Form Score",
    "score_consistency":"Consistency",
    "score_reliability":"Reliability",
    "score_trajectory": "Trajectory",
    "all_conditions":   "All-Conditions",
    "n_disciplines":    "Events",
    "scout_rating":     "Scout Rating",
}

table = df[list(display_cols.keys())].rename(columns=display_cols)
table["YOB"]   = table["YOB"].astype("Int64")
table["Age"]   = table["Age"].astype("Int64")
table["Events"]= table["Events"].astype("Int64")

st.dataframe(
    table,
    use_container_width=True,
    column_config={
        "Scout Rating": st.column_config.ProgressColumn(
            "Scout Rating", format="%.1f", min_value=0, max_value=100,
            help="Weighted composite: 40% form + 25% consistency + 20% reliability + 15% trajectory. Percentile within current pool.",
        ),
        "Form Score": st.column_config.ProgressColumn(
            "Form Score", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of career mean z-score within this pool. 100 = best form.",
        ),
        "Consistency": st.column_config.ProgressColumn(
            "Consistency", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of trend-adjusted std of z-scores. 100 = most reliable. Improving athletes get credit for their upward trend.",
        ),
        "Reliability": st.column_config.ProgressColumn(
            "Reliability", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of inverse DNF/DSQ/DNS rate. 100 = always finishes.",
        ),
        "Trajectory": st.column_config.ProgressColumn(
            "Trajectory", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of momentum z (recent vs career baseline). 100 = strongest upward momentum in pool.",
        ),
        "Form (z)":      st.column_config.NumberColumn("Form (z)",      format="%+.3f", help="Career mean z-score. 0=field avg; +0.3=above avg; +0.7=elite tier."),
        "Avg FIS":       st.column_config.NumberColumn("Avg FIS",       format="%.1f",  help="Career average FIS points. Lower = faster."),
        "Best FIS":      st.column_config.NumberColumn("Best FIS",      format="%.1f",  help="Career-best (lowest ever) FIS points. Their ceiling to date."),
        "Career Races":  st.column_config.NumberColumn("Career Races",  help="Total career starts across all seasons."),
        "All-Conditions":st.column_config.TextColumn("All-Conditions",  help="Performance spread across weather bins. Excellent (<0.20 std) → Variable (>0.55 std). 'Limited' = fewer than 3 weather bins on record."),
        "Events":        st.column_config.NumberColumn("Events",        help="Number of disciplines with 3+ career starts."),
        "Age":           st.column_config.NumberColumn("Age",           help=f"Age as of {CURRENT_YEAR}."),
    },
    height=min(650, 55 + 35 * len(table)),
)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

st.divider()

# ── Row 1: Form vs Consistency scatter + Radar ───────────────────────────────
scatter_col, radar_col = st.columns([3, 2])

with scatter_col:
    st.subheader("Form vs Consistency")
    st.caption("Top-right = fast and reliable (target zone). Bubble size = career races. Color = Scout Rating.")

    fig_s = px.scatter(
        df,
        x="mean_z",
        y="score_consistency",
        size="race_count",
        color="scout_rating",
        hover_name="name",
        hover_data={
            "country": True, "yob": True, "race_count": True,
            "mean_z": ":.3f", "mean_fis": ":.1f", "best_fis": ":.1f",
            "dnf_pct": ":.1f", "scout_rating": ":.1f",
        },
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
        size_max=28,
        labels={
            "mean_z": "Form — Career mean z-score",
            "score_consistency": "Consistency Score (0–100, trend-adjusted)",
            "scout_rating": "Scout Rating",
            "race_count": "Career Races",
        },
        template="plotly_white",
    )
    fig_s.add_vline(x=0, line_dash="dot", line_color="gray", opacity=0.4)
    fig_s.update_traces(marker_opacity=0.78)
    fig_s.update_layout(
        height=420,
        coloraxis_colorbar=dict(title="Scout Rating"),
        margin=dict(l=50, r=20, t=20, b=50),
    )
    st.plotly_chart(fig_s, use_container_width=True)


with radar_col:
    st.subheader("Athlete Spotlight")

    sel_name = st.selectbox(
        "Select athlete",
        df["name"].tolist(),
        index=0,
        label_visibility="collapsed",
    )
    sel = df[df["name"] == sel_name].iloc[0]

    cats   = ["Form", "Consistency", "Reliability", "Trajectory"]
    vals   = [float(sel["score_form"]), float(sel["score_consistency"]),
              float(sel["score_reliability"]), float(sel["score_trajectory"])]
    fig_r = go.Figure(go.Scatterpolar(
        r     = vals + [vals[0]],
        theta = cats + [cats[0]],
        fill  = "toself",
        fillcolor = "rgba(26, 58, 107, 0.18)",
        line  = dict(color="#1a3a6b", width=2),
    ))
    fig_r.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100],
                           tickvals=[25, 50, 75, 100], tickfont=dict(size=10), gridcolor="#ddd"),
            angularaxis=dict(tickfont=dict(size=13)),
        ),
        showlegend=False, height=300,
        margin=dict(l=40, r=40, t=20, b=10),
        paper_bgcolor="white",
    )
    st.plotly_chart(fig_r, use_container_width=True)

    traj_word = _trajectory_label(sel.get("momentum_z"))
    best_fis  = f"{sel['best_fis']:.1f}" if pd.notna(sel.get("best_fis")) else "—"
    st.markdown(f"""
**{sel_name}** · {sel.get('country','') or ''} · Age {int(sel['age'])} (born {int(sel['yob'])}) · {sel['discipline']}

| | |
|---|---|
| Scout Rating | **{sel['scout_rating']:.1f}** / 100 |
| Form (z) | **{sel['mean_z']:+.3f}** |
| Avg FIS | **{sel['mean_fis']:.1f}** |
| Best FIS | **{best_fis}** |
| Consistency | **{sel['score_consistency']:.0f}** / 100 |
| Reliability | **{sel['dnf_pct']:.1f}%** DNF |
| Trajectory | **{traj_word}** |
| All-conditions | **{sel['all_conditions']}** |
| Career races | **{int(sel['race_count'])}** |
| Events | **{int(sel['n_disciplines']) if pd.notna(sel.get('n_disciplines')) else 1}** |
    """)


# ── Row 2: Scout Rating breakdown bar chart (top 20) ────────────────────────
st.divider()
st.subheader("Scout Rating Breakdown — Top 20")
st.caption("Each bar shows the weighted contribution of each component to the Scout Rating.")

top20 = df.head(20).copy().sort_values("scout_rating", ascending=True)
top20["contrib_form"]        = (W_FORM        * top20["score_form"]).round(1)
top20["contrib_consistency"] = (W_CONSISTENCY * top20["score_consistency"]).round(1)
top20["contrib_reliability"] = (W_RELIABILITY * top20["score_reliability"]).round(1)
top20["contrib_trajectory"]  = (W_TRAJECTORY  * top20["score_trajectory"]).round(1)

fig_b = go.Figure()
components = [
    ("Form",        "contrib_form",        "#1a3a6b"),
    ("Consistency", "contrib_consistency",  "#2e6da4"),
    ("Reliability", "contrib_reliability",  "#5ba3d0"),
    ("Trajectory",  "contrib_trajectory",   "#a8d4f0"),
]
for label, col, color in components:
    fig_b.add_trace(go.Bar(
        name=label,
        y=top20["name"],
        x=top20[col],
        orientation="h",
        marker_color=color,
        hovertemplate=f"<b>%{{y}}</b><br>{label}: %{{x:.1f}} pts<extra></extra>",
    ))

fig_b.update_layout(
    barmode="stack",
    xaxis=dict(title="Weighted contribution to Scout Rating (max 100)"),
    yaxis=dict(title="", automargin=True),
    height=max(350, 28 * len(top20)),
    margin=dict(l=180, r=40, t=10, b=50),
    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
    plot_bgcolor="white", paper_bgcolor="white",
)
fig_b.update_xaxes(showgrid=True, gridcolor="#eee", range=[0, 100])
fig_b.update_yaxes(showgrid=False)
st.plotly_chart(fig_b, use_container_width=True)


# ── Row 3: Age vs Form scatter ───────────────────────────────────────────────
st.divider()
st.subheader("Age vs Form")
st.caption("Younger athletes with strong form are the highest-upside prospects.")

fig_a = px.scatter(
    df,
    x="age",
    y="mean_z",
    size="race_count",
    color="scout_rating",
    hover_name="name",
    hover_data={
        "country": True, "yob": True, "race_count": True,
        "mean_z": ":.3f", "mean_fis": ":.1f", "scout_rating": ":.1f",
    },
    color_continuous_scale="RdYlGn",
    range_color=[0, 100],
    size_max=24,
    labels={
        "age":          "Age",
        "mean_z":       "Form — Career mean z-score",
        "scout_rating": "Scout Rating",
        "race_count":   "Career Races",
    },
    template="plotly_white",
)
fig_a.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.4)
fig_a.update_traces(marker_opacity=0.78)
fig_a.update_layout(
    height=360,
    coloraxis_colorbar=dict(title="Scout Rating"),
    margin=dict(l=50, r=20, t=10, b=50),
    xaxis=dict(tickmode="linear", dtick=1),
)
st.plotly_chart(fig_a, use_container_width=True)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

st.divider()
csv_cols = [
    "name", "country", "yob", "age", "discipline", "race_type", "race_count",
    "mean_z", "std_z", "mean_fis", "best_fis", "dnf_pct", "momentum_z",
    "score_form", "score_consistency", "score_reliability", "score_trajectory",
    "all_conditions", "n_disciplines", "scout_rating",
]
csv_out = df.reset_index()[[c for c in csv_cols if c in df.columns]].to_csv(index=False)
st.download_button(
    "Download board as CSV",
    data=csv_out,
    file_name=f"recruiting_{gender_choice.replace(' ','_')}_{disc_choice}.csv",
    mime="text/csv",
)
