"""
Alpine Analytics — Recruiting Board

FIS Development Index — rolling 18-month window.
Scoring inspired by golf handicap (best-N-of-last-M), tennis ATP (rolling window),
and FM potential ratings (separate current level from trajectory).

FIS points are field-adjusted by definition (F-value formula) so they are
directly comparable across race types — unlike z-score which punishes athletes
who seek out tougher competition.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from database import query


# ─── Constants ────────────────────────────────────────────────────────────────

CURRENT_YEAR   = 2026
ROLLING_MONTHS = 18
N_PEAK         = 5    # golf-handicap: avg of best N results in window
N_STD          = 10   # consistency: std of best N results

# FIS age group bands — percentile scoring is always within the selected group
AGE_GROUPS = {
    "U16 (born 2010+)":   (2010, 9999),
    "U18 (born 2008–09)": (2008, 2009),
    "U21 (born 2005–07)": (2005, 2007),
}
_ALL_MIN_YOB = 2005   # oldest eligible athlete for data load

# Race difficulty weights (0–100) — used to credit athletes racing tougher fields
RACE_LEVEL_WEIGHT = {
    "World Cup":                          100,
    "Audi FIS Ski World Cup":             100,
    "World Cup Speed Event":              100,
    "Olympic Winter Games":               100,
    "World Championships":                100,
    "FIS Junior World Ski Championships":  90,
    "European Cup":                        80,
    "European Cup Speed Event":            80,
    "CIT":                                 75,
    "CIT Arnold Lunn World Cup":           75,
    "Nor-Am Cup":                          65,
    "South American Cup":                  65,
    "Australian New Zealand Cup":          65,
    "Far East Cup":                        65,
    "Asian Winter Games":                  65,
    "FIS":                                 50,
    "FIS Qualification":                   50,
    "Entry League FIS":                    45,
    "National Championships":              40,
    "University":                          35,
    "National Junior Championships":       35,
    "National Junior Race":                30,
}
_DEFAULT_WEIGHT = 40

RACE_LEVEL_GROUPS = {
    "All levels": None,
    "World Cup":       ["World Cup", "World Cup Speed Event", "Audi FIS Ski World Cup",
                        "Olympic Winter Games", "World Championships"],
    "European Cup":    ["European Cup", "European Cup Speed Event",
                        "CIT", "CIT Arnold Lunn World Cup"],
    "Continental Cup": ["Nor-Am Cup", "South American Cup",
                        "Australian New Zealand Cup", "Far East Cup", "Asian Winter Games"],
    "FIS / Junior":    ["FIS", "FIS Junior World Ski Championships", "FIS Qualification",
                        "National Junior Championships", "National Junior Race",
                        "National Championships", "Entry League FIS"],
}

DISCIPLINES = ["Slalom", "Giant Slalom", "Super G", "Downhill", "Alpine Combined"]

# Scout Rating component weights — must sum to 1.0
# Level is king — exceptional FIS points should always dominate the ranking.
# Comp level is essential context: same FIS pts at WC vs NJR are not equal.
# Trajectory: are they still growing? Bonus for improving, not a penalty for arriving.
# Hit Rate is a warning signal shown as a reference column, not in the composite —
# ratio metrics have small-sample noise that corrupts rankings at this pool size.
W_PEAK        = 0.65
W_COMP_LEVEL  = 0.20
W_TRAJECTORY  = 0.15


# ─── Data loader ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=604800)
def load_rolling_races() -> pd.DataFrame:
    """Per-race rows for all athletes born >= MIN_YOB in the rolling window."""
    return query(f"""
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
                fis_code::text AS fis_code, sex
            FROM (
                SELECT fr.fis_code::text AS fis_code, rd.sex, COUNT(*) AS cnt
                FROM raw.fis_results fr
                JOIN raw.race_details rd ON rd.race_id = fr.race_id
                WHERE rd.sex IS NOT NULL
                GROUP BY fr.fis_code, rd.sex
            ) g
            ORDER BY fis_code, cnt DESC
        ),
        career AS (
            SELECT
                fr.fis_code::text                                       AS fis_code,
                rd.discipline,
                COUNT(*)                                                AS career_races,
                MIN(CASE WHEN fr.fis_points > 0 THEN fr.fis_points END) AS career_best_fis
            FROM raw.fis_results fr
            JOIN raw.race_details rd ON rd.race_id = fr.race_id
            GROUP BY fr.fis_code, rd.discipline
        )
        SELECT
            fr.fis_code::text            AS fis_code,
            fr.name,
            yc.yob,
            yc.country,
            gm.sex,
            rd.discipline,
            rd.race_type,
            rd.date,
            fr.fis_points,
            ca.career_races,
            ca.career_best_fis
        FROM raw.fis_results fr
        JOIN raw.race_details rd   ON rd.race_id  = fr.race_id
        JOIN yob_country       yc  ON yc.fis_code = fr.fis_code::text
        JOIN gender_map        gm  ON gm.fis_code = fr.fis_code::text
        LEFT JOIN career       ca  ON ca.fis_code  = fr.fis_code::text
                                   AND ca.discipline = rd.discipline
        WHERE yc.yob >= {_ALL_MIN_YOB}
          AND rd.date >= CURRENT_DATE - INTERVAL '{ROLLING_MONTHS} months'
          AND rd.discipline IS NOT NULL AND rd.discipline <> ''
    """)


# ─── Rolling metric computation ───────────────────────────────────────────────

def _group_metrics(g: pd.DataFrame) -> dict | None:
    """Compute rolling window metrics for one athlete+discipline group."""
    g = g.sort_values("date")
    n_total   = len(g)
    finished  = g[g["fis_points"].notna() & (g["fis_points"] > 0)]
    n_fin     = len(finished)
    if n_fin == 0:
        return None

    dnf_pct   = round((n_total - n_fin) / n_total * 100, 1)
    pts       = finished["fis_points"].values

    # Peak FIS — avg of best N_PEAK results (golf handicap style)
    peak_fis  = float(np.sort(pts)[:min(N_PEAK, n_fin)].mean())

    # Consistency — std of best N_STD results (ceiling reliability)
    n_std     = min(N_STD, n_fin)
    rolling_std = float(np.sort(pts)[:n_std].std()) if n_std >= 2 else 0.0

    # Trajectory — linear slope of FIS points over time, normalised to % of mean/month
    # Negative slope = FIS points are dropping = athlete is getting faster
    if n_fin >= 3:
        days = (finished["date"] - finished["date"].min()).dt.days.values.astype(float)
        if days.max() > 7:
            slope, _ = np.polyfit(days, pts, 1)
            mean_pts  = pts.mean()
            fis_trend = (slope * 30 / mean_pts * 100) if mean_pts > 0 else 0.0
        else:
            fis_trend = 0.0
    else:
        fis_trend = 0.0

    comp_level  = float(g["race_level_weight"].mean())
    mean_fis    = float(pts.mean())

    # Ceiling hit rate — how close is their avg to their own peak?
    ceiling_ratio = round(peak_fis / mean_fis, 4) if mean_fis > 0 else 1.0

    # Highest competition level reached (in finished races within window)
    best_idx              = finished["fis_points"].idxmin()
    best_race_level_score = float(finished.loc[best_idx, "race_level_weight"])
    best_race_type        = str(finished.loc[best_idx, "race_type"])

    # Human-readable competition ceiling label
    if best_race_level_score >= 100:
        best_level_label = "WC"
    elif best_race_level_score >= 80:
        best_level_label = "EC+"
    elif best_race_level_score >= 65:
        best_level_label = "Continental"
    elif best_race_level_score >= 50:
        best_level_label = "FIS"
    else:
        best_level_label = "Junior/Nat"

    return {
        "rolling_races":       n_total,
        "n_finished":          n_fin,
        "dnf_pct":             dnf_pct,
        "peak_fis":            round(peak_fis, 1),
        "ceiling_ratio":       ceiling_ratio,
        "rolling_std":         round(rolling_std, 1),
        "fis_trend":           round(fis_trend, 3),       # negative = improving
        "improvement_rate":    round(-fis_trend, 3),      # positive = improving (display)
        "comp_level":          round(comp_level, 1),
        "rolling_mean_fis":    round(mean_fis, 1),
        "best_race_level":     best_race_level_score,
        "best_race_type":      best_race_type,
        "best_level_label":    best_level_label,
        "career_races":        int(g["career_races"].iloc[0]) if g["career_races"].notna().any() else n_total,
        "career_best_fis":     round(float(g["career_best_fis"].dropna().min()), 1)
                               if g["career_best_fis"].notna().any() else round(peak_fis, 1),
    }


def build_athlete_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse per-race rows into one summary row per athlete+discipline."""
    raw = raw.copy()
    raw["date"]              = pd.to_datetime(raw["date"])
    raw["race_level_weight"] = raw["race_type"].map(RACE_LEVEL_WEIGHT).fillna(_DEFAULT_WEIGHT)

    records = []
    for (fis_code, discipline), g in raw.groupby(["fis_code", "discipline"], sort=False):
        metrics = _group_metrics(g)
        if metrics is None:
            continue
        row = g.iloc[0]
        records.append({
            "fis_code":   fis_code,
            "name":       row["name"],
            "yob":        int(row["yob"]) if pd.notna(row["yob"]) else None,
            "country":    row["country"],
            "sex":        row["sex"],
            "discipline": discipline,
            **metrics,
        })

    return pd.DataFrame(records) if records else pd.DataFrame()


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    """Percentile rank 0–100. ascending=True → higher raw value = higher score."""
    n = len(series)
    if n <= 1:
        return pd.Series([100.0] * n, index=series.index)
    ranked = series.rank(method="average", ascending=ascending, na_option="bottom")
    return ((ranked - 1) / (n - 1) * 100).round(1)


def compute_scout_rating(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # 1. Level — lower FIS points = better. Dominant signal.
    df["score_peak"]       = _pct_rank(df["peak_fis"],   ascending=False)

    # 2. Competition level — higher = racing tougher fields.
    df["score_comp_level"] = _pct_rank(df["comp_level"], ascending=True)

    # 3. Trajectory — BONUS ONLY. Floor at 50 so declining athletes are neutral,
    #    not penalised. Elite athletes at ceiling legitimately have flat trends.
    raw_traj               = _pct_rank(df["fis_trend"],  ascending=False)
    df["score_trajectory"] = raw_traj.clip(lower=50)

    # Status label — tells scout whether athlete is climbing, topped out, etc.
    def _status(row):
        if row["peak_fis"] <= 20:
            return "Elite"
        if row["improvement_rate"] >= 2.0:
            return "Climbing"
        if row["improvement_rate"] <= -2.0:
            return "Declining"
        return "Stable"

    df["status"] = df.apply(_status, axis=1)

    df["scout_rating"] = (
        W_PEAK       * df["score_peak"]
        + W_COMP_LEVEL * df["score_comp_level"]
        + W_TRAJECTORY * df["score_trajectory"]
    ).round(1)
    return df


# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(page_title="Recruiting Board — Alpine Analytics", layout="wide")
st.title("Recruiting Board")
st.markdown(
    "**FIS Development Index** — rolling 18-month window. "
    "Scores peak level, trajectory, competition level, and consistency. "
    "FIS points are race-adjusted by definition — comparable across race types "
    "so athletes are not penalized for seeking tougher competition."
)

with st.expander("How Scout Rating is calculated", expanded=False):
    st.markdown(f"""
**Scout Rating** is a 0–100 composite, percentile-ranked within whoever is in the current filter pool.

| Component | Weight | Method |
|---|---|---|
| **Peak Level** | {W_PEAK:.0%} | Average of your **best {N_PEAK} FIS results** in the last {ROLLING_MONTHS} months. Golf-handicap style — one hard race does not tank the score. Lower FIS = better. Absolute performance level is the dominant signal. |
| **Competition Level** | {W_COMP_LEVEL:.0%} | Weighted avg of every race entered (WC=100, EC=80, Nor-Am/CIT=65, FIS=50, NJR=30). The same FIS points at a WC field mean more than at a thin NJR. Athletes who seek out tougher races get credit. |
| **Trajectory** | {W_TRAJECTORY:.0%} | FIS points slope as % of mean/month. **Bonus only — floor at neutral (50).** Athletes who have already arrived at ceiling are not penalised for a flat trend. Declining athletes get neutral, not negative. |

**Hit Rate (`Peak FIS ÷ Avg FIS`) is shown as a reference column, not in the composite.** Ratio metrics have small-sample noise that corrupts rankings at junior pool sizes. Use it yourself as a "fat race" filter: peak=17, avg=17, hit=100% → consistently elite. Peak=30, avg=70, hit=43% → scrutinise before committing.

**Column guide:**

| Column | Meaning |
|---|---|
| **Peak FIS** | Avg of best {N_PEAK} FIS points in the rolling {ROLLING_MONTHS}-month window. Primary level indicator. Lower = faster. |
| **Best Ever** | All-time career-best single FIS result. Their absolute ceiling. |
| **Avg FIS (rolling)** | Mean of all finishes in rolling window. Compare with Peak FIS to spot inconsistency. |
| **Hit Rate** | Peak FIS ÷ Avg FIS as %. 90%+ = near-ceiling consistently. Below 65% = big gap, check the race list. Reference only. |
| **Trend (%/mo)** | FIS improvement per month as % of their mean. Positive = getting faster. +2.5 means improving ~2.5%/month. |
| **Comp. Level** | Avg race level 0–100. 80+ = EC/WC circuit. 50 = FIS. 35 = junior national. |
| **DNF %** | % not finished. Reference — not in Scout Rating. |
| **Scout Rating** | Composite: {W_PEAK:.0%} Peak Level + {W_COMP_LEVEL:.0%} Comp. Level + {W_TRAJECTORY:.0%} Trajectory. 100 = best in current pool. Top 250 shown. |

**Reading the board:**
Strong Peak FIS + high Hit Rate + high Comp. Level = the real deal. Recruit without hesitation.
Strong Peak FIS + low Hit Rate = had a great day or two — check Avg FIS and race list before committing.
Modest Peak FIS + strong Trajectory = still climbing. Project forward: where are they in 12 months?
High Comp. Level + modest FIS at young age = racing hard fields early. Discount the FIS slightly vs pure FIS-circuit peers.
    """)


# ─── Load raw race data ───────────────────────────────────────────────────────

with st.spinner("Loading athlete data..."):
    raw_df = load_rolling_races()

if raw_df.empty:
    st.error("No data available.")
    st.stop()


# ─── Sidebar filters ──────────────────────────────────────────────────────────

st.sidebar.header("Filters")

gender_choice  = st.sidebar.radio("Gender", ["Men's", "Women's"], horizontal=True)
disc_choice    = st.sidebar.selectbox("Discipline", DISCIPLINES, index=0)
age_group_name = st.sidebar.radio("Age Group", list(AGE_GROUPS.keys()))
level_choice   = st.sidebar.selectbox("Race Level", list(RACE_LEVEL_GROUPS.keys()))

# Race type and gender filters are applied to raw race rows before building
# the athlete table — this controls which races feed into the metrics
race_types   = RACE_LEVEL_GROUPS[level_choice]
filtered_raw = raw_df[raw_df["sex"] == gender_choice].copy()
if race_types is not None:
    filtered_raw = filtered_raw[filtered_raw["race_type"].isin(race_types)]
filtered_raw = filtered_raw[filtered_raw["discipline"] == disc_choice]

if filtered_raw.empty:
    st.info("No athletes match the current filters.")
    st.stop()

# Build per-athlete summary from filtered races
df_all = build_athlete_table(filtered_raw)

if df_all.empty:
    st.info("No athletes with sufficient data match the current filters.")
    st.stop()

# Filter to selected age group
yob_min, yob_max = AGE_GROUPS[age_group_name]
df_all = df_all[df_all["yob"].notna() & df_all["yob"].between(yob_min, yob_max)].copy()

if df_all.empty:
    st.info("No athletes in the selected age group.")
    st.stop()

min_races      = st.sidebar.slider("Min races (rolling window)", min_value=1, max_value=20, value=5)
country_search = st.sidebar.text_input("Filter by country (e.g. USA, AUT)").strip().upper()


# ─── Apply remaining filters ──────────────────────────────────────────────────

df = df_all[df_all["rolling_races"] >= min_races].copy()
if country_search:
    df = df[df["country"].str.upper().str.contains(country_search, na=False)]

if df.empty:
    st.info("No athletes match the current filters. Try relaxing the requirements.")
    st.stop()

# ─── Score and sort ───────────────────────────────────────────────────────────

df = compute_scout_rating(df)
df = df.sort_values("scout_rating", ascending=False).head(250).reset_index(drop=True)
df.index += 1
df.index.name = "Rank"
df["age"] = CURRENT_YEAR - df["yob"].astype(int)


# ─── Leaderboard table ────────────────────────────────────────────────────────

st.subheader("Leaderboard")

display_cols = {
    "name":             "Name",
    "country":          "Country",
    "yob":              "YOB",
    "age":              "Age",
    "status":           "Status",
    "rolling_races":    "Races (18mo)",
    "peak_fis":         "Peak FIS",
    "career_best_fis":  "Best Ever",
    "best_level_label": "Best At",
    "rolling_mean_fis": "Avg FIS (rolling)",
    "hit_rate_pct":     "Hit Rate",
    "improvement_rate": "Trend (%/mo)",
    "comp_level":       "Comp. Level",
    "dnf_pct":          "DNF %",
    "score_peak":       "Level Score",
    "score_comp_level": "Comp. Score",
    "score_trajectory": "Trajectory",
    "scout_rating":     "Scout Rating",
}

df["hit_rate_pct"] = (df["ceiling_ratio"] * 100).round(1)

table = df[[c for c in display_cols.keys() if c in df.columns]].rename(columns=display_cols)
table["YOB"]          = table["YOB"].astype("Int64")
table["Age"]          = table["Age"].astype("Int64")
table["Races (18mo)"] = table["Races (18mo)"].astype("Int64")

st.dataframe(
    table,
    use_container_width=True,
    column_config={
        "Scout Rating": st.column_config.ProgressColumn(
            "Scout Rating", format="%.1f", min_value=0, max_value=100,
            help=f"Composite: {W_PEAK:.0%} Peak Level + {W_COMP_LEVEL:.0%} Comp. Level + "
                 f"{W_TRAJECTORY:.0%} Trajectory. Percentile within current pool.",
        ),
        "Level Score": st.column_config.ProgressColumn(
            "Level Score", format="%.0f", min_value=0, max_value=100,
            help=f"Percentile rank of Peak FIS (avg best {N_PEAK}). 100 = fastest in pool.",
        ),
        "Comp. Score": st.column_config.ProgressColumn(
            "Comp. Score", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of avg competition level. 100 = consistently races toughest fields.",
        ),
        "Trajectory": st.column_config.ProgressColumn(
            "Trajectory", format="%.0f", min_value=0, max_value=100,
            help="Percentile rank of FIS improvement slope. 100 = fastest rate of improvement. Bonus for climbers, not a penalty for established elites.",
        ),
        "Hit Rate": st.column_config.NumberColumn(
            "Hit Rate", format="%.1f%%",
            help="Peak FIS ÷ Avg FIS as %. Reference — not in composite. 90%+ = near-ceiling consistently. Below 65% = big spread, check race history.",
        ),
        "Peak FIS":          st.column_config.NumberColumn(
            "Peak FIS", format="%.1f",
            help=f"Avg of best {N_PEAK} FIS points in the rolling {ROLLING_MONTHS}-month window. Lower = faster.",
        ),
        "Best Ever":         st.column_config.NumberColumn(
            "Best Ever", format="%.1f",
            help="All-time career-best FIS result. Their absolute ceiling.",
        ),
        "Avg FIS (rolling)": st.column_config.NumberColumn(
            "Avg FIS (rolling)", format="%.1f",
            help="Mean of all finishes in the rolling window. Includes hard-field results — reference only.",
        ),
        "Trend (%/mo)":      st.column_config.NumberColumn(
            "Trend (%/mo)", format="%+.1f",
            help="FIS points improvement rate per month, as % of their mean. Positive = getting faster. "
                 "e.g. +2.5 means dropping ~2.5% of their avg FIS points every month.",
        ),
        "Comp. Level":       st.column_config.NumberColumn(
            "Comp. Level", format="%.0f",
            help="Avg race level 0–100. WC=100, EC=80, Nor-Am/CIT=65, FIS=50, NJR=30.",
        ),
        "DNF %":             st.column_config.NumberColumn(
            "DNF %", format="%.1f",
            help="% of races not finished. Reference only — not included in Scout Rating.",
        ),
        "Best At":           st.column_config.TextColumn(
            "Best At",
            help="Highest competition level at which they scored their best FIS result in the window. WC / EC+ / Continental / FIS / Junior-Nat.",
        ),
        "Status":            st.column_config.TextColumn(
            "Status",
            help="Elite = peak FIS ≤ 20. Climbing = improving >2%/mo. Declining = worsening >2%/mo. Stable = otherwise.",
        ),
        "Races (18mo)":      st.column_config.NumberColumn(
            "Races (18mo)", help="Starts in the rolling 18-month window.",
        ),
        "Age":               st.column_config.NumberColumn(
            "Age", help=f"Age as of {CURRENT_YEAR}.",
        ),
    },
    height=min(650, 55 + 35 * len(table)),
)


# ─── Charts ───────────────────────────────────────────────────────────────────

st.divider()

# Row 1: Peak FIS vs Trajectory scatter  +  Athlete Radar
scatter_col, radar_col = st.columns([3, 2])

with scatter_col:
    st.subheader("Peak Level vs Trajectory")
    st.caption(
        "Top-right = already fast AND improving quickly. "
        "Y-axis inverted: lower FIS (better) appears higher. "
        "Bubble size = races in rolling window. Color = Scout Rating."
    )
    fig_s = px.scatter(
        df,
        x="improvement_rate",
        y="peak_fis",
        size="rolling_races",
        color="scout_rating",
        hover_name="name",
        hover_data={
            "country": True, "yob": True, "rolling_races": True,
            "peak_fis": ":.1f", "career_best_fis": ":.1f",
            "comp_level": ":.0f", "dnf_pct": ":.1f",
            "scout_rating": ":.1f",
        },
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
        size_max=28,
        labels={
            "improvement_rate": "Improvement Rate (%/mo) — positive = getting faster",
            "peak_fis":         "Peak FIS (lower = faster)",
            "scout_rating":     "Scout Rating",
            "rolling_races":    "Races (18mo)",
        },
        template="plotly_white",
    )
    fig_s.add_vline(x=0, line_dash="dot", line_color="gray", opacity=0.4)
    fig_s.update_yaxes(autorange="reversed")   # lower FIS = top of chart
    fig_s.update_traces(marker_opacity=0.78)
    fig_s.update_layout(
        height=420,
        coloraxis_colorbar=dict(title="Scout Rating"),
        margin=dict(l=50, r=20, t=20, b=60),
    )
    st.plotly_chart(fig_s, use_container_width=True)


with radar_col:
    st.subheader("Athlete Spotlight")
    sel_name = st.selectbox(
        "Select athlete", df["name"].tolist(), index=0, label_visibility="collapsed"
    )
    sel = df[df["name"] == sel_name].iloc[0]

    cats = ["Peak Level", "Comp. Level", "Trajectory"]
    vals = [
        float(sel["score_peak"]),
        float(sel["score_comp_level"]),
        float(sel["score_trajectory"]),
    ]
    fig_r = go.Figure(go.Scatterpolar(
        r=vals + [vals[0]], theta=cats + [cats[0]],
        fill="toself",
        fillcolor="rgba(26, 58, 107, 0.18)",
        line=dict(color="#1a3a6b", width=2),
    ))
    fig_r.update_layout(
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 100],
                tickvals=[25, 50, 75, 100], tickfont=dict(size=10), gridcolor="#ddd",
            ),
            angularaxis=dict(tickfont=dict(size=12)),
        ),
        showlegend=False, height=300,
        margin=dict(l=40, r=40, t=20, b=10),
        paper_bgcolor="white",
    )
    st.plotly_chart(fig_r, use_container_width=True)

    best_ever  = f"{sel['career_best_fis']:.1f}" if pd.notna(sel.get("career_best_fis")) else "—"
    trend_str  = f"{sel['improvement_rate']:+.1f}%/mo"
    hit_rate   = f"{sel['hit_rate_pct']:.1f}%"
    st.markdown(f"""
**{sel_name}** · {sel.get('country','') or ''} · Age {int(sel['age'])} (born {int(sel['yob'])}) · {sel['discipline']}

| | |
|---|---|
| Scout Rating | **{sel['scout_rating']:.1f}** / 100 |
| Status | **{sel.get('status','—')}** |
| Peak FIS ({N_PEAK}-race avg) | **{sel['peak_fis']:.1f}** |
| Best At | **{sel.get('best_level_label','—')}** ({sel.get('best_race_type','—')}) |
| Avg FIS (rolling) | **{sel['rolling_mean_fis']:.1f}** |
| Ceiling Hit Rate | **{hit_rate}** |
| Career Best FIS | **{best_ever}** |
| Trend | **{trend_str}** |
| Competition Level | **{sel['comp_level']:.0f}** / 100 |
| DNF rate | **{sel['dnf_pct']:.1f}%** |
| Races (18mo) | **{int(sel['rolling_races'])}** |
| Career races | **{int(sel['career_races'])}** |
    """)


# Row 2: Scout Rating breakdown — top 20
st.divider()
st.subheader("Scout Rating Breakdown — Top 20")
st.caption("Weighted contribution of each component to the Scout Rating.")

top20 = df.head(20).copy().sort_values("scout_rating", ascending=True)
top20["contrib_peak"] = (W_PEAK        * top20["score_peak"]).round(1)
top20["contrib_comp"] = (W_COMP_LEVEL  * top20["score_comp_level"]).round(1)
top20["contrib_traj"] = (W_TRAJECTORY  * top20["score_trajectory"]).round(1)

fig_b = go.Figure()
for label, col, color in [
    ("Peak Level",  "contrib_peak", "#1a3a6b"),
    ("Comp. Level", "contrib_comp", "#2e6da4"),
    ("Trajectory",  "contrib_traj", "#5ba3d0"),
]:
    fig_b.add_trace(go.Bar(
        name=label, y=top20["name"], x=top20[col], orientation="h",
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


# Row 3: Age vs Peak FIS
st.divider()
st.subheader("Age vs Peak Level")
st.caption(
    "Younger athletes with strong FIS points are the highest-upside prospects. "
    "Y-axis inverted: lower FIS (better) appears higher."
)
fig_a = px.scatter(
    df,
    x="age",
    y="peak_fis",
    size="rolling_races",
    color="scout_rating",
    hover_name="name",
    hover_data={
        "country": True, "yob": True, "rolling_races": True,
        "peak_fis": ":.1f", "career_best_fis": ":.1f",
        "comp_level": ":.0f", "scout_rating": ":.1f",
    },
    color_continuous_scale="RdYlGn",
    range_color=[0, 100],
    size_max=24,
    labels={
        "age":         "Age",
        "peak_fis":    "Peak FIS (lower = faster)",
        "scout_rating":"Scout Rating",
        "rolling_races":"Races (18mo)",
    },
    template="plotly_white",
)
fig_a.update_yaxes(autorange="reversed")
fig_a.update_traces(marker_opacity=0.78)
fig_a.update_layout(
    height=360,
    coloraxis_colorbar=dict(title="Scout Rating"),
    margin=dict(l=50, r=20, t=10, b=50),
    xaxis=dict(tickmode="linear", dtick=1),
)
st.plotly_chart(fig_a, use_container_width=True)


# ─── Download ─────────────────────────────────────────────────────────────────

st.divider()
csv_cols = [
    "name", "country", "yob", "age", "discipline",
    "status", "rolling_races", "career_races",
    "peak_fis", "career_best_fis", "best_level_label", "best_race_type",
    "rolling_mean_fis", "hit_rate_pct",
    "improvement_rate", "comp_level", "dnf_pct",
    "score_peak", "score_comp_level", "score_trajectory",
    "scout_rating",
]
csv_out = df.reset_index()[[c for c in csv_cols if c in df.columns]].to_csv(index=False)
st.download_button(
    "Download board as CSV",
    data=csv_out,
    file_name=f"recruiting_{gender_choice.replace(' ', '_')}_{disc_choice}.csv",
    mime="text/csv",
)
