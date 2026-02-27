"""
Alpine Analytics — Athlete Profile
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from database import query


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def smart_sort_key(val):
    """Sort trait bin labels: numeric ranges by first number, else alphabetically."""
    import re
    s = str(val).strip()
    match = re.search(r"-?\d+\.?\d*", s)
    if match:
        return (0, float(match.group()))
    order = {"low": 0, "medium": 1, "mid": 1, "high": 2}
    return (1, order.get(s.lower(), 99))


def bins_to_ordinal(bins) -> dict:
    """Map sorted bin labels to evenly spaced values from -1 to +1."""
    sorted_bins = sorted(bins, key=smart_sort_key)
    n = len(sorted_bins)
    if n == 1:
        return {sorted_bins[0]: 0.0}
    return {b: round(-1 + 2 * i / (n - 1), 2) for i, b in enumerate(sorted_bins)}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_athlete_list() -> list[str]:
    df = query("""
        SELECT DISTINCT name
        FROM athlete_aggregate.hot_streak
        WHERE name IS NOT NULL AND name != ''
        ORDER BY name
    """)
    return df["name"].tolist()


@st.cache_data(ttl=604800)
def load_athlete_by_fis_id(fis_id: str) -> str | None:
    """Look up athlete name by FIS ID."""
    try:
        df = query("""
            SELECT DISTINCT name
            FROM athletes
            WHERE CAST(fis_code AS TEXT) = :fis_id
            LIMIT 1
        """, {"fis_id": fis_id.strip()})
        if not df.empty:
            return str(df.iloc[0, 0])
    except Exception:
        pass
    return None


@st.cache_data(ttl=604800)
def load_fis_code(name: str) -> str | None:
    try:
        df = query("""
            SELECT DISTINCT fis_code
            FROM athletes
            WHERE name = :name
            LIMIT 1
        """, {"name": name})
        if not df.empty:
            return str(df.iloc[0, 0])
    except Exception:
        pass
    return None


@st.cache_data(ttl=604800)
def load_hot_streak(name: str) -> pd.DataFrame:
    df = query("""
        SELECT race_id, date, discipline, fis_points, rank,
               race_z_score, momentum_z, momentum_fis, rolling_race_z
        FROM athlete_aggregate.hot_streak
        WHERE name = :name
        ORDER BY date
    """, {"name": name})
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=604800)
def load_career_stats(name: str) -> pd.DataFrame:
    return query("""
        SELECT discipline, race_count,
               mean_fis_points, min_fis_points,
               mean_rank, dnf_rate, mean_race_z_score
        FROM athlete_aggregate.basic_athlete_info_career
        WHERE name = :name
        ORDER BY race_count DESC
    """, {"name": name})


@st.cache_data(ttl=604800)
def load_yearly_stats(name: str) -> pd.DataFrame:
    df = query("""
        SELECT race_year, discipline, race_count,
               mean_fis_points, mean_rank, mean_race_z_score
        FROM athlete_aggregate.basic_athlete_info_yearly
        WHERE name = :name
        ORDER BY race_year, discipline
    """, {"name": name})
    df["race_year"] = df["race_year"].astype(int)
    return df


@st.cache_data(ttl=604800)
def load_performance_tier(name: str) -> pd.DataFrame:
    return query("""
        SELECT discipline, year, tier, avg_fis_points, race_count
        FROM athlete_aggregate.performance_tiers
        WHERE name = :name
        ORDER BY year DESC, race_count DESC
    """, {"name": name})


@st.cache_data(ttl=604800)
def load_course_traits(name: str) -> pd.DataFrame:
    return query("""
        SELECT discipline, trait, trait_bin,
               avg_performance_delta, avg_z_score, race_count
        FROM athlete_aggregate.course_traits
        WHERE name = :name
        ORDER BY discipline, trait, race_count DESC
    """, {"name": name})


@st.cache_data(ttl=604800)
def load_strokes_gained(name: str) -> pd.DataFrame:
    df = query("""
        SELECT race_id, date, discipline, location, fis_points,
               points_gained, race_z_score
        FROM race_aggregate.strokes_gained
        WHERE name = :name
        ORDER BY date DESC
    """, {"name": name})
    df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=604800)
def load_top_performances(name: str) -> pd.DataFrame:
    try:
        df = query("""
            SELECT date, discipline, location, fis_points,
                   points_gained, race_z_score
            FROM race_aggregate.strokes_gained
            WHERE name = :name AND fis_points IS NOT NULL
            ORDER BY fis_points ASC
            LIMIT 20
        """, {"name": name})
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=604800)
def load_consistency_stats(name: str) -> pd.DataFrame:
    try:
        return query("""
            SELECT discipline, dnf_rate, max_dnf_streak,
                   bounce_back_z_score, re_dnf_rate, cv_fis
            FROM athlete_aggregate.performance_consistency_career
            WHERE name = :name
            ORDER BY discipline
        """, {"name": name})
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=604800)
def load_consistency_yearly(name: str) -> pd.DataFrame:
    try:
        return query("""
            SELECT discipline, season, n_races, std_race_z_score, cv_fis
            FROM athlete_aggregate.performance_consistency_yearly
            WHERE name = :name
            ORDER BY discipline, season
        """, {"name": name})
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=604800)
def load_location_stats(name: str) -> pd.DataFrame:
    """Aggregate performance per venue — used for the Best Hills table."""
    try:
        df = query("""
            SELECT
                location,
                discipline,
                COUNT(*)                                              AS race_count,
                AVG(fis_points)                                       AS avg_fis_points,
                MIN(fis_points)                                       AS best_fis_points,
                AVG(points_gained)                                    AS avg_pts_gained,
                AVG(race_z_score)                                     AS avg_z_score,
                MAX(date)                                             AS last_raced
            FROM race_aggregate.strokes_gained
            WHERE name = :name
              AND location IS NOT NULL
            GROUP BY location, discipline
            ORDER BY avg_z_score DESC
        """, {"name": name})
        if not df.empty:
            df["last_raced"] = pd.to_datetime(df["last_raced"])
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=604800)
def load_race_field_stats(name: str) -> pd.DataFrame:
    """Field-level FIS point distribution for each race the athlete competed in."""
    try:
        df = query("""
            WITH athlete_races AS (
                SELECT DISTINCT race_id, date, discipline, location
                FROM race_aggregate.strokes_gained
                WHERE name = :name
            )
            SELECT
                ar.race_id,
                ar.date,
                ar.discipline,
                ar.location,
                AVG(sg.fis_points)  AS field_avg_fis,
                MIN(sg.fis_points)  AS field_best_fis,
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY sg.fis_points) AS field_p25_fis,
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY sg.fis_points) AS field_p75_fis
            FROM athlete_races ar
            JOIN race_aggregate.strokes_gained sg
                ON sg.race_id = ar.race_id
            GROUP BY ar.race_id, ar.date, ar.discipline, ar.location
            ORDER BY ar.date
        """, {"name": name})
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=604800)
def load_bib_relative_stats(name: str) -> pd.DataFrame:
    """
    For each race the athlete ran, get the average strokes gained of athletes
    within ±5 bib numbers — true bib-adjacent competitors who start under similar
    conditions and face the same course state.
    """
    try:
        df = query("""
            WITH athlete_races AS (
                SELECT sg.race_id, sg.date, sg.discipline, sg.location,
                       r.bib AS athlete_bib,
                       sg.points_gained AS athlete_sg
                FROM race_aggregate.strokes_gained sg
                JOIN raw.fis_results r
                    ON r.race_id = sg.race_id AND r.name = sg.name
                WHERE sg.name = :name
                  AND r.bib IS NOT NULL
            )
            SELECT
                ar.race_id,
                ar.date,
                ar.discipline,
                ar.location,
                ar.athlete_sg,
                AVG(sg.points_gained)   AS peer_avg_sg,
                COUNT(DISTINCT sg.name) AS peer_count
            FROM athlete_races ar
            JOIN race_aggregate.strokes_gained sg
                ON sg.race_id = ar.race_id
               AND sg.name != :name
            JOIN raw.fis_results r
                ON r.race_id = sg.race_id AND r.name = sg.name
               AND r.bib BETWEEN ar.athlete_bib - 5 AND ar.athlete_bib + 5
            GROUP BY ar.race_id, ar.date, ar.discipline, ar.location, ar.athlete_sg
            ORDER BY ar.date
        """, {"name": name})
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Sidebar — athlete selector + discipline filter + section navigation
# ---------------------------------------------------------------------------

st.sidebar.title("Athlete Profile")

try:
    athlete_names = load_athlete_list()
except Exception as e:
    st.error(f"Could not connect to database: {e}")
    st.stop()

if not athlete_names:
    st.warning("No athlete data found. Run the ETL pipeline first.")
    st.stop()

search_mode = st.sidebar.radio("Search by", ["Name", "FIS ID"], horizontal=True, key="athlete_search_mode")

if search_mode == "FIS ID":
    fis_input = st.sidebar.text_input("FIS ID", placeholder="e.g. 512182", key="fis_id_input")
    if fis_input.strip():
        resolved_name = load_athlete_by_fis_id(fis_input.strip())
        if resolved_name and resolved_name in athlete_names:
            default_idx = athlete_names.index(resolved_name)
        else:
            st.sidebar.warning("No athlete found for that FIS ID.")
            default_idx = 0
    else:
        default_idx = 0
    selected = st.sidebar.selectbox("Athlete", athlete_names, index=default_idx, key="athlete_select_fis")
else:
    selected = st.sidebar.selectbox("Search athlete", athlete_names, index=0, key="athlete_select_name")

st.sidebar.markdown("---")

with st.spinner(f"Loading {selected}..."):
    df_streak       = load_hot_streak(selected)
    df_career       = load_career_stats(selected)
    df_yearly       = load_yearly_stats(selected)
    df_tier         = load_performance_tier(selected)
    df_traits       = load_course_traits(selected)
    df_sg           = load_strokes_gained(selected)
    df_field        = load_race_field_stats(selected)
    df_bib          = load_bib_relative_stats(selected)
    df_locs         = load_location_stats(selected)
    fis_code        = load_fis_code(selected)
    df_top          = load_top_performances(selected)
    df_consistency  = load_consistency_stats(selected)
    df_cons_yearly  = load_consistency_yearly(selected)

if df_streak.empty:
    st.warning("No data found for this athlete.")
    st.stop()

disciplines = sorted(df_streak["discipline"].dropna().unique().tolist())
selected_disc = st.sidebar.multiselect("Disciplines", disciplines, default=disciplines)

st.sidebar.markdown("---")

SECTIONS = [
    "Overview",
    "Year-by-Year",
    "Course Traits",
    "Hot Streak",
    "Consistency & Bounce Back",
    "Strokes Gained",
    "Top Performances",
    "Best Hills",
]
section = st.sidebar.radio("Navigate", SECTIONS, label_visibility="collapsed")

# Apply discipline filter
streak_f       = df_streak[df_streak["discipline"].isin(selected_disc)]
career_f       = df_career[df_career["discipline"].isin(selected_disc)]
yearly_f       = df_yearly[df_yearly["discipline"].isin(selected_disc)]
tier_f         = df_tier[df_tier["discipline"].isin(selected_disc)]
traits_f       = df_traits[df_traits["discipline"].isin(selected_disc)]
sg_f           = df_sg[df_sg["discipline"].isin(selected_disc)]
field_f        = df_field[df_field["discipline"].isin(selected_disc)] if not df_field.empty else df_field
bib_f          = df_bib[df_bib["discipline"].isin(selected_disc)] if not df_bib.empty else df_bib
locs_f         = df_locs[df_locs["discipline"].isin(selected_disc)] if not df_locs.empty else df_locs
top_f          = df_top[df_top["discipline"].isin(selected_disc)] if not df_top.empty else df_top
consistency_f  = df_consistency[df_consistency["discipline"].isin(selected_disc)] if not df_consistency.empty else df_consistency
cons_yearly_f  = df_cons_yearly[df_cons_yearly["discipline"].isin(selected_disc)] if not df_cons_yearly.empty else df_cons_yearly

# ---------------------------------------------------------------------------
# Page header — name + FIS code
# ---------------------------------------------------------------------------

h1, h2 = st.columns([5, 1])
h1.title(selected)
if fis_code:
    h2.metric("FIS Code", fis_code)

st.markdown("---")


# ===========================================================================
# Overview
# ===========================================================================
if section == "Overview":

    st.subheader("Career Summary")
    st.caption(
        "Top-line career numbers by discipline. "
        "Best FIS is the single lowest (best) FIS points result ever posted — the career ceiling. "
        "Avg FIS is the mean across all finishes, which reflects typical output rather than peak. "
        "Avg Z-Score is the most important number here: above 0 means the athlete beats the field "
        "more often than not across their entire career. Below 0 means they are consistently below "
        "the field average. DNF % counts races where the athlete did not reach the finish line."
    )

    if not career_f.empty:
        card_cols = st.columns(min(len(career_f), 4))
        for col, (_, row) in zip(card_cols, career_f.iterrows()):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{row['discipline']}**")
                    c1, c2 = st.columns(2)
                    c1.metric("Races",    int(row["race_count"]))
                    c2.metric("Best FIS", f"{row['min_fis_points']:.1f}")
                    c3, c4 = st.columns(2)
                    c3.metric("Avg FIS",  f"{row['mean_fis_points']:.1f}")
                    c4.metric("Avg Z",    f"{row['mean_race_z_score']:.3f}")
                    st.metric("DNF %",    f"{row['dnf_rate']*100:.1f}%")
    else:
        st.info("No career stats available.")

    # FIS Points Over Time
    st.markdown("---")
    st.subheader("FIS Points Over Time")
    st.caption(
        "Each dot is one race. Lower FIS points = better result. "
        "The trend line (LOWESS) shows the overall trajectory — downward = improving, upward = declining."
    )
    if not streak_f.empty:
        fig = px.scatter(
            streak_f,
            x="date", y="fis_points",
            color="discipline",
            trendline="lowess",
            labels={"fis_points": "FIS Points", "date": ""},
        )
        fig.update_layout(height=340, margin=dict(t=20, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

    # All race results table
    st.markdown("---")
    st.subheader("All Race Results")
    st.caption(
        "Complete race history, most recent first. "
        "Z-Score measures performance vs the full field that day — positive = beat the field. "
        "Momentum Z tracks whether recent results are trending up or down."
    )
    if not streak_f.empty:
        display = streak_f.sort_values("date", ascending=False)[
            ["date", "discipline", "fis_points", "rank", "race_z_score", "momentum_z"]
        ].copy()
        display["date"]        = display["date"].dt.strftime("%Y-%m-%d")
        display["fis_points"]  = display["fis_points"].round(1)
        display["race_z_score"] = display["race_z_score"].round(3)
        display["momentum_z"]  = display["momentum_z"].round(3)
        display.columns = ["Date", "Discipline", "FIS Pts", "Rank", "Z-Score", "Momentum Z"]
        st.dataframe(display, use_container_width=True, hide_index=True, height=420)
    else:
        st.info("No race results available.")

    # FIS Points vs Z-Score
    st.markdown("---")
    st.subheader("FIS Points vs Z-Score")
    st.caption(
        "Each dot is one race. X-axis = Z-Score (positive = beat the field). "
        "Y-axis = FIS points (lower = better finish). "
        "The trend line shows how closely this athlete's raw FIS points track their field-relative performance. "
        "A steep negative slope means their FIS points are a reliable indicator of how they compare to the field."
    )
    if not streak_f.empty:
        fig_fz = px.scatter(
            streak_f.dropna(subset=["fis_points", "race_z_score"]),
            x="race_z_score", y="fis_points",
            color="discipline",
            trendline="ols",
            hover_data={"date": True, "rank": True},
            labels={"race_z_score": "Z-Score vs Field", "fis_points": "FIS Points"},
        )
        fig_fz.add_vline(x=0, line_dash="dot", line_color="gray")
        fig_fz.update_layout(height=400, margin=dict(t=20, b=0), legend_title_text="")
        st.plotly_chart(fig_fz, use_container_width=True)


# ===========================================================================
# Year-by-Year
# ===========================================================================
elif section == "Year-by-Year":

    st.subheader("Year-by-Year Breakdown")
    st.caption(
        "Season-level averages per discipline, covering every start in each calendar year. "
        "Avg Z-Score is the primary indicator: a season above 0 means the athlete beat the field "
        "more often than not that year. A trend of rising Z-Scores across multiple seasons signals "
        "genuine long-term development. A declining FIS average paired with an improving Z-Score "
        "often indicates an athlete moving into stronger, more competitive fields — a sign of progress, "
        "not decline. Watch both together to distinguish real improvement from schedule changes."
    )

    if not yearly_f.empty:
        display = yearly_f.copy()
        display.columns = ["Year", "Discipline", "Races", "Avg FIS", "Avg Rank", "Avg Z-Score"]
        display["Avg FIS"]     = display["Avg FIS"].round(1)
        display["Avg Rank"]    = display["Avg Rank"].round(1)
        display["Avg Z-Score"] = display["Avg Z-Score"].round(3)
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("Avg FIS Points by Year")
        st.caption("Lower = better. A downward trend means the athlete is improving.")
        fig = px.line(
            yearly_f, x="race_year", y="mean_fis_points", color="discipline",
            markers=True,
            labels={"mean_fis_points": "Avg FIS Points", "race_year": "Year"},
        )
        fig.update_layout(height=340, margin=dict(t=20, b=0), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")
        st.subheader("Avg Z-Score by Year")
        st.caption("Positive = the athlete outperformed the field that season on average. 0 = exactly average.")
        fig2 = px.line(
            yearly_f, x="race_year", y="mean_race_z_score", color="discipline",
            markers=True,
            labels={"mean_race_z_score": "Avg Z-Score", "race_year": "Year"},
        )
        fig2.add_hline(y=0, line_dash="dot", line_color="gray")
        fig2.update_layout(height=320, margin=dict(t=20, b=0), legend_title_text="")
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No yearly data available.")


# ===========================================================================
# Course Traits
# ===========================================================================
elif section == "Course Traits":

    st.subheader("Course Trait Impact")
    st.caption(
        "How course physical characteristics systematically affect this athlete's performance. "
        "Courses are grouped into five bins (quintiles) for each trait — lowest to highest. "
        "The summary bar chart ranks traits by total impact: a longer bar means that "
        "characteristic produces the biggest swing in this athlete's Z-Score from one extreme "
        "to the other. Blue = performs better as that trait increases (e.g., better on higher "
        "gate-count courses). Orange = performs better when the trait is lower. "
        "Gray = no consistent directional effect. "
        "The detail charts below break down each trait bin by bin. A large positive bar in "
        "a specific bin means this athlete consistently outperforms their own career average "
        "on courses in that range — a genuine structural strength, not a random result."
    )

    if not traits_f.empty:
        for disc in sorted(traits_f["discipline"].unique()):
            disc_data = traits_f[traits_f["discipline"] == disc]
            if disc_data.empty:
                continue

            st.markdown(f"**{disc}**")

            # --- Impact magnitude summary chart ---
            impact_rows = []
            for trait in disc_data["trait"].unique():
                t_data = disc_data[disc_data["trait"] == trait].copy()
                if len(t_data) < 2:
                    continue
                bin_map = bins_to_ordinal(t_data["trait_bin"].unique())
                t_data["ordinal"] = t_data["trait_bin"].map(bin_map)
                t_data = t_data.sort_values("ordinal")
                delta = t_data.iloc[-1]["avg_z_score"] - t_data.iloc[0]["avg_z_score"]
                magnitude = t_data["avg_z_score"].max() - t_data["avg_z_score"].min()
                impact_rows.append({
                    "trait":     trait,
                    "label":     trait.replace("_", " ").title(),
                    "magnitude": magnitude,
                    "delta":     delta,
                })

            if impact_rows:
                imp_df = (
                    pd.DataFrame(impact_rows)
                    .sort_values("magnitude", ascending=True)  # longest bar at top
                )
                bar_colors = [
                    "#1f77b4" if d > 0.05 else "#e07b39" if d < -0.05 else "#888888"
                    for d in imp_df["delta"]
                ]
                dir_labels = [
                    "Better when Higher" if d > 0.05
                    else "Better when Lower" if d < -0.05
                    else "No Clear Impact"
                    for d in imp_df["delta"]
                ]
                fig_imp = go.Figure(go.Bar(
                    y=imp_df["label"],
                    x=imp_df["magnitude"],
                    orientation="h",
                    marker_color=bar_colors,
                    text=dir_labels,
                    textposition="outside",
                    hovertemplate="<b>%{y}</b><br>Impact range: %{x:.3f}<extra></extra>",
                ))
                fig_imp.update_layout(
                    height=max(200, len(imp_df) * 45),
                    margin=dict(t=10, b=10, l=20, r=160),
                    xaxis_title="Performance Impact (Z-Score range across bins)",
                    showlegend=False,
                )
                st.plotly_chart(fig_imp, use_container_width=True)

            st.markdown("---")

            # --- Per-trait detail charts (bins sorted low → high, left → right) ---
            for trait in sorted(disc_data["trait"].unique()):
                t_data = disc_data[disc_data["trait"] == trait].copy()
                if len(t_data) < 2:
                    continue

                bin_map = bins_to_ordinal(t_data["trait_bin"].unique())
                t_data["ordinal"] = t_data["trait_bin"].map(bin_map)
                t_data = t_data.sort_values("ordinal")  # lowest → highest left to right
                sorted_bins = t_data["trait_bin"].astype(str).tolist()

                bar_colors = [
                    "#1f77b4" if v >= 0 else "#d62728"
                    for v in t_data["avg_z_score"]
                ]
                fig = go.Figure(go.Bar(
                    x=t_data["trait_bin"].astype(str),
                    y=t_data["avg_z_score"],
                    marker_color=bar_colors,
                    text=t_data["avg_z_score"].round(3),
                    textposition="outside",
                    customdata=t_data["race_count"].values,
                    hovertemplate=(
                        "Bin: %{x}<br>Avg Z: %{y:.3f}<br>Races: %{customdata}<extra></extra>"
                    ),
                ))
                fig.add_hline(y=0, line_dash="dot", line_color="gray")
                fig.update_layout(
                    title=trait.replace("_", " ").title(),
                    height=240,
                    margin=dict(t=35, b=10, l=20, r=10),
                    yaxis_title="Avg Z-Score",
                    xaxis=dict(
                        categoryorder="array",
                        categoryarray=sorted_bins,
                        title="",
                    ),
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No course trait data available for selected disciplines.")


# ===========================================================================
# Hot Streak
# ===========================================================================
elif section == "Hot Streak":

    st.subheader("Hot Streak — Momentum")
    st.caption(
        "Current form, independent of career averages. The bold line is a smoothed rolling average "
        "of recent Z-Scores — it filters out single-race noise to show the underlying trend. "
        "The faint line behind it is the raw, unsmoothed signal. "
        "A line rising above zero means the athlete is currently in a run of above-average results. "
        "A line falling toward or below zero means form is cooling, regardless of what their "
        "FIS points or career Z-Score say. This is the most relevant indicator of near-term performance. "
        "An athlete with a career Z of +0.2 but sharply rising momentum is often more dangerous "
        "heading into the next race than one with a higher average whose momentum is declining."
    )

    if not streak_f.empty:
        smooth = streak_f.copy()
        smooth["momentum_smooth"] = (
            smooth.groupby("discipline")["momentum_z"]
            .transform(lambda x: x.rolling(3, min_periods=1, center=True).mean())
        )

        fig = go.Figure()
        colors = px.colors.qualitative.Plotly
        for i, disc in enumerate(sorted(smooth["discipline"].unique())):
            sub = smooth[smooth["discipline"] == disc].sort_values("date")
            c = colors[i % len(colors)]
            # Raw — faint
            fig.add_trace(go.Scatter(
                x=sub["date"], y=sub["momentum_z"],
                mode="lines", name=f"{disc} (raw)",
                opacity=0.2, line=dict(width=1, color=c),
                showlegend=False,
            ))
            # Smoothed — bold
            fig.add_trace(go.Scatter(
                x=sub["date"], y=sub["momentum_smooth"],
                mode="lines", name=disc,
                line=dict(width=2.5, color=c),
            ))
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(
            height=360, margin=dict(t=20, b=0),
            yaxis_title="Momentum (3-race rolling avg)",
            xaxis_title="", legend_title_text="",
        )
        st.plotly_chart(fig, use_container_width=True)

        # Race-by-race table
        st.markdown("---")
        st.subheader("Race-by-Race Data")
        st.caption(
            "Raw data behind the momentum chart. "
            "Rolling Z is a moving average of recent Z-Scores — above 0 means a recent run of above-average races. "
            "Momentum Z is the rate of change; a rising value means the trend is accelerating upward."
        )
        table_data = streak_f.sort_values("date", ascending=False)[
            ["date", "discipline", "fis_points", "rank", "race_z_score", "momentum_z", "rolling_race_z"]
        ].copy()
        table_data["date"]          = table_data["date"].dt.strftime("%Y-%m-%d")
        table_data["fis_points"]    = table_data["fis_points"].round(1)
        table_data["race_z_score"]  = table_data["race_z_score"].round(3)
        table_data["momentum_z"]    = table_data["momentum_z"].round(3)
        table_data["rolling_race_z"] = table_data["rolling_race_z"].round(3)
        table_data.columns = ["Date", "Disc", "FIS Pts", "Rank", "Z-Score", "Momentum Z", "Rolling Z"]
        st.dataframe(table_data, use_container_width=True, hide_index=True, height=420)
    else:
        st.info("No hot streak data for selected disciplines.")


# ===========================================================================
# Consistency & Bounce Back
# ===========================================================================
elif section == "Consistency & Bounce Back":

    st.subheader("Consistency & Bounce Back")

    if not streak_f.empty:
        st.subheader("Race-to-Race Consistency")
        st.caption(
            "Distribution of Z-Scores across every race in the selected disciplines. "
            "The shape of the violin reveals the athlete's reliability profile. "
            "Narrow and tall, centered above 0: highly consistent, above-average performer — "
            "rarely dominant but rarely far off either. "
            "Wide and spread out: high-variance athlete who can dominate one race and fall well "
            "below average in the next. "
            "The horizontal box marks the middle 50% of results; the line inside is the median. "
            "Individual dots are single races — outlier dots far from the body reveal the ceiling "
            "and floor of this athlete's range. A consistently positive median with a narrow spread "
            "is the profile of a reliable, field-beating competitor."
        )
        fig = px.violin(
            streak_f.dropna(subset=["race_z_score"]),
            x="discipline", y="race_z_score",
            color="discipline", box=True, points="all",
            labels={"race_z_score": "Z-Score vs Field", "discipline": ""},
        )
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=400, margin=dict(t=20, b=0), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        stat_cols = st.columns(len(streak_f["discipline"].unique()))
        for col, disc in zip(stat_cols, sorted(streak_f["discipline"].unique())):
            sub = streak_f[streak_f["discipline"] == disc]["race_z_score"].dropna()
            if len(sub) < 2:
                continue
            # Pull career CV from consistency table
            cv_row = consistency_f[consistency_f["discipline"] == disc]
            cv_val = cv_row["cv_fis"].iloc[0] if not cv_row.empty and pd.notna(cv_row["cv_fis"].iloc[0]) else None
            with col:
                with st.container(border=True):
                    st.markdown(f"**{disc}**")
                    st.metric("Std Dev (Z)", f"{sub.std():.3f}",
                              help="Standard deviation of Z-Scores across all career starts. Lower = more consistent race-to-race.")
                    st.metric("% Above Avg", f"{(sub > 0).mean()*100:.0f}%",
                              help="Share of career starts where the athlete beat the field average.")
                    if cv_val is not None:
                        st.metric("CV (FIS)", f"{cv_val:.2f}",
                                  help="Coefficient of Variation of FIS points (std / mean). Lower = results are tightly clustered around career average. Higher = wide swings between dominant and poor races.")

        st.markdown("---")
        st.subheader("Consistency Over Time")
        st.caption(
            "Standard deviation of Z-Scores per season. "
            "A low value means results were tightly clustered around the athlete's average that year — "
            "they delivered a reliable, predictable level of performance. "
            "A high value means wide swings: dominant one race, well below average the next. "
            "Tracking this year to year reveals whether an athlete is becoming more or less reliable as their career progresses."
        )
        if not cons_yearly_f.empty:
            cy = cons_yearly_f.dropna(subset=["std_race_z_score"]).copy()
            cy["season"] = cy["season"].astype(str)
            # Build one trace per discipline
            fig_cy = go.Figure()
            palette = px.colors.qualitative.Plotly
            for i, disc in enumerate(sorted(cy["discipline"].unique())):
                sub = cy[cy["discipline"] == disc].sort_values("season")
                fig_cy.add_trace(go.Bar(
                    x=sub["season"],
                    y=sub["std_race_z_score"].round(3),
                    name=disc,
                    marker_color=palette[i % len(palette)],
                    text=sub["n_races"].apply(lambda n: f"n={int(n)}"),
                    textposition="outside",
                    hovertemplate=(
                        "<b>" + disc + " %{x}</b><br>"
                        "Std Dev Z: %{y:.3f}<br>"
                        "Races: %{text}<extra></extra>"
                    ),
                ))
            fig_cy.update_layout(
                height=380,
                margin=dict(t=20, b=0),
                barmode="group",
                xaxis_title="Season",
                yaxis_title="Std Dev of Z-Score",
                legend=dict(orientation="h", y=1.08),
            )
            st.plotly_chart(fig_cy, use_container_width=True)
        else:
            st.info("No yearly consistency data for selected disciplines.")

        st.markdown("---")
        st.subheader("Bounce Back After a Bad Race")
        st.caption(
            "After a below-average race (Z-Score below –0.3), how often does the athlete "
            "follow with an above-average result? "
            "The base rate (gray bar) is the athlete's unconditional probability of posting "
            "an above-average race on any given start — so a bounce-back rate that exceeds "
            "the base rate means the athlete genuinely responds better after a setback, "
            "not just that they are generally a strong performer."
        )
        bounce_rows = []
        for disc in sorted(streak_f["discipline"].unique()):
            sub = streak_f[streak_f["discipline"] == disc].sort_values("date")
            z = sub["race_z_score"].dropna().values
            if len(z) < 3:
                continue
            bad      = z[:-1] < -0.3
            nxt_good = z[1:] > 0
            base_rate = float((z > 0).mean())  # unconditional good-race rate
            if bad.sum() > 0:
                bounce_rows.append({
                    "Discipline":        disc,
                    "Bounce Back Rate":  float((bad & nxt_good).sum() / bad.sum()),
                    "Base Rate":         base_rate,
                    "Bad Races Sampled": int(bad.sum()),
                })
        if bounce_rows:
            bb_df = pd.DataFrame(bounce_rows)
            fig_bb = go.Figure()
            fig_bb.add_trace(go.Bar(
                x=bb_df["Discipline"],
                y=bb_df["Base Rate"],
                name="Base Rate (any race)",
                marker_color="#aaaaaa",
                text=bb_df["Base Rate"].apply(lambda v: f"{v:.0%}"),
                textposition="outside",
            ))
            fig_bb.add_trace(go.Bar(
                x=bb_df["Discipline"],
                y=bb_df["Bounce Back Rate"],
                name="Bounce Back Rate",
                marker_color="#1f77b4",
                text=bb_df["Bounce Back Rate"].apply(lambda v: f"{v:.0%}"),
                textposition="outside",
            ))
            fig_bb.update_yaxes(tickformat=".0%", range=[0, 1.25])
            fig_bb.update_layout(
                height=340, margin=dict(t=20, b=0),
                barmode="group",
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_bb, use_container_width=True)

            # Summary metrics
            metric_cols = st.columns(len(bb_df))
            for col, (_, row) in zip(metric_cols, bb_df.iterrows()):
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{row['Discipline']}**")
                        delta = row["Bounce Back Rate"] - row["Base Rate"]
                        st.metric(
                            "Bounce Back Rate",
                            f"{row['Bounce Back Rate']:.0%}",
                            delta=f"{delta:+.0%} vs base",
                            help=f"Base rate (any race above avg): {row['Base Rate']:.0%}",
                        )
                        st.caption(f"n = {row['Bad Races Sampled']} bad races")
        else:
            st.info("Not enough race data to compute bounce back rates.")

        st.markdown("---")
        st.subheader("After a DNF or DSQ")
        st.caption(
            "When an athlete doesn't finish a race, two questions matter: "
            "do they tend to DNF again next time out, and how do they actually perform "
            "when they do come back and finish? "
            "Re-DNF Rate is the fraction of races immediately following a DNF/DSQ "
            "where the athlete DNF'd or DSQ'd again. "
            "Bounce-Back Z is their average Z-Score in the race after a DNF/DSQ — "
            "above 0 means they come back performing above the field average."
        )
        if not consistency_f.empty:
            dnf_cols = st.columns(min(len(consistency_f), 5))
            for col, (_, row) in zip(dnf_cols, consistency_f.iterrows()):
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{row['discipline']}**")
                        re_dnf = row.get("re_dnf_rate")
                        bb_z   = row.get("bounce_back_z_score")
                        if pd.notna(re_dnf):
                            st.metric(
                                "Re-DNF Rate",
                                f"{re_dnf:.0%}",
                                help="% of races right after a DNF/DSQ where they DNF'd/DSQ'd again",
                            )
                        else:
                            st.metric("Re-DNF Rate", "—")
                        if pd.notna(bb_z):
                            st.metric(
                                "Bounce-Back Z",
                                f"{bb_z:+.3f}",
                                help="Avg Z-Score in the race after a DNF/DSQ. Above 0 = came back above field average.",
                            )
                        else:
                            st.metric("Bounce-Back Z", "—")
        else:
            st.info("No DNF data available for selected disciplines.")

        st.markdown("---")
        st.subheader("Recent Race Z-Scores")
        st.caption(
            "Each bar is one race (last 30 per discipline). "
            "Blue = the athlete outperformed the field that day. "
            "Red = underperformed. Useful for spotting short-term hot or cold streaks."
        )
        for disc in sorted(streak_f["discipline"].unique()):
            sub = streak_f[streak_f["discipline"] == disc].sort_values("date").tail(30)
            if sub.empty:
                continue
            st.markdown(f"**{disc}**")
            colors = ["#1f77b4" if v > 0 else "#d62728" for v in sub["race_z_score"].fillna(0)]
            fig_seq = go.Figure(go.Bar(
                x=sub["date"], y=sub["race_z_score"],
                marker_color=colors,
                hovertemplate="Date: %{x}<br>Z-Score: %{y:.3f}<extra></extra>",
            ))
            fig_seq.add_hline(y=0, line_dash="dot", line_color="gray")
            fig_seq.update_layout(height=240, margin=dict(t=10, b=0))
            st.plotly_chart(fig_seq, use_container_width=True)
    else:
        st.info("No race data for selected disciplines.")


# ===========================================================================
# Strokes Gained
# ===========================================================================
elif section == "Strokes Gained":

    st.subheader("Strokes Gained vs Field")
    st.caption(
        "Strokes Gained measures how many FIS points the athlete gained or lost relative to "
        "the field average in each race. Positive = gained on the field; negative = lost ground."
    )

    if not sg_f.empty:

        # --- Overlay: athlete FIS vs field distribution ---
        st.subheader("Results in Context")
        st.caption(
            "This athlete's FIS points (blue line) overlaid on the field for each race. "
            "The dashed gray line is the field average; the dotted green line is the top 25% cutoff. "
            "The shaded band is the middle 50% of finishers — athletes 'near' the field median. "
            "Lower FIS = better. The closer the blue line is to the green, the stronger the result."
        )

        if not field_f.empty:
            merged = (
                sg_f.sort_values("date")
                .merge(
                    field_f[["race_id",
                              "field_avg_fis", "field_best_fis",
                              "field_p25_fis", "field_p75_fis"]],
                    on="race_id",
                    how="left",
                )
            )

            for disc in sorted(merged["discipline"].unique()):
                sub = merged[merged["discipline"] == disc].sort_values("date")
                if sub.empty:
                    continue

                st.markdown(f"**{disc}**")
                fig = go.Figure()

                # Shaded band: middle 50% of field (p25 to p75)
                if "field_p25_fis" in sub.columns and "field_p75_fis" in sub.columns:
                    fig.add_trace(go.Scatter(
                        x=pd.concat([sub["date"], sub["date"].iloc[::-1]]),
                        y=pd.concat([sub["field_p25_fis"], sub["field_p75_fis"].iloc[::-1]]),
                        fill="toself",
                        fillcolor="rgba(180,180,180,0.15)",
                        line=dict(color="rgba(0,0,0,0)"),
                        name="Middle 50% of Field",
                        hoverinfo="skip",
                    ))

                # Field average
                if "field_avg_fis" in sub.columns:
                    fig.add_trace(go.Scatter(
                        x=sub["date"], y=sub["field_avg_fis"],
                        mode="lines", name="Field Average",
                        line=dict(color="gray", dash="dash", width=1.5),
                    ))

                # Top 25%
                if "field_p25_fis" in sub.columns:
                    fig.add_trace(go.Scatter(
                        x=sub["date"], y=sub["field_p25_fis"],
                        mode="lines", name="Top 25% of Field",
                        line=dict(color="#2ca02c", dash="dot", width=1.5),
                    ))

                # Athlete
                fig.add_trace(go.Scatter(
                    x=sub["date"], y=sub["fis_points"],
                    mode="markers+lines", name="Athlete",
                    marker=dict(size=7, color="#1f77b4"),
                    line=dict(color="#1f77b4", width=2),
                ))

                fig.update_yaxes(autorange="reversed", title="FIS Points (lower = better)")
                fig.update_layout(
                    height=360,
                    margin=dict(t=20, b=0),
                    xaxis_title="",
                    legend=dict(orientation="h", y=1.1),
                )
                st.plotly_chart(fig, use_container_width=True)
        else:
            # Fallback: simple bar chart
            sg_sorted = sg_f.sort_values("date")
            colors = ["#1f77b4" if v > 0 else "#d62728" for v in sg_sorted["points_gained"].fillna(0)]
            fig = go.Figure(go.Bar(
                x=sg_sorted["date"], y=sg_sorted["points_gained"],
                marker_color=colors,
            ))
            fig.add_hline(y=0, line_dash="dot", line_color="gray")
            fig.update_layout(height=340, margin=dict(t=20, b=0),
                              yaxis_title="Points Gained vs Field")
            st.plotly_chart(fig, use_container_width=True)

        # --- Bib-relative graph ---
        st.markdown("---")
        st.subheader("Bib-Relative Performance")
        st.caption(
            "In ski racing, bib numbers are assigned by FIS points ranking — lower bib = higher-ranked athlete. "
            "Athletes with nearby bibs start in similar conditions and face a comparable course state. "
            "This chart compares the athlete's strokes gained (blue) against the average strokes gained "
            "of athletes who started within 5 bibs of them in each race (orange dashed). "
            "When the blue line is above the orange, the athlete outperformed their immediate starting-order peers. "
            "Peers are defined as the 10 athletes bracketing this athlete's bib (±5), not the full field."
        )
        if not bib_f.empty:
            for disc in sorted(bib_f["discipline"].unique()):
                sub = bib_f[bib_f["discipline"] == disc].sort_values("date")
                if sub.empty:
                    continue
                st.markdown(f"**{disc}**")
                fig_bib = go.Figure()

                # Shaded band between athlete and peer avg
                fig_bib.add_trace(go.Scatter(
                    x=pd.concat([sub["date"], sub["date"].iloc[::-1]]),
                    y=pd.concat([sub["athlete_sg"], sub["peer_avg_sg"].iloc[::-1]]),
                    fill="toself",
                    fillcolor="rgba(31,119,180,0.1)",
                    line=dict(color="rgba(0,0,0,0)"),
                    name="Gap vs Peers",
                    hoverinfo="skip",
                ))

                # Zero reference (field average)
                fig_bib.add_hline(y=0, line_dash="dot", line_color="gray",
                                  annotation_text="Field avg",
                                  annotation_position="bottom right")

                # Peer avg line
                fig_bib.add_trace(go.Scatter(
                    x=sub["date"], y=sub["peer_avg_sg"],
                    mode="lines", name="±5 Bibs Avg",
                    line=dict(color="#ff7f0e", dash="dash", width=1.5),
                    hovertemplate="Date: %{x}<br>±5 Bibs Avg SG: %{y:.1f}<extra></extra>",
                ))

                # Athlete line
                fig_bib.add_trace(go.Scatter(
                    x=sub["date"], y=sub["athlete_sg"],
                    mode="markers+lines", name="Athlete",
                    marker=dict(size=7, color="#1f77b4"),
                    line=dict(color="#1f77b4", width=2),
                    customdata=sub["peer_count"].values,
                    hovertemplate=(
                        "Date: %{x}<br>Athlete SG: %{y:.1f}<br>"
                        "Peers compared: %{customdata}<extra></extra>"
                    ),
                ))

                fig_bib.update_layout(
                    height=360,
                    margin=dict(t=20, b=0),
                    yaxis_title="Points Gained vs Field",
                    xaxis_title="",
                    legend=dict(orientation="h", y=1.1),
                )
                st.plotly_chart(fig_bib, use_container_width=True)
        else:
            st.info("Bib-relative data not available.")

        # Average SG by discipline
        st.markdown("---")
        st.subheader("Average Strokes Gained by Discipline")
        st.caption(
            "Average points gained per race in each discipline. "
            "Positive = the athlete typically gains on the field in this event. "
            "Negative = they typically lose ground."
        )
        athlete_avg = sg_f.groupby("discipline")["points_gained"].mean().reset_index()
        athlete_avg.columns = ["Discipline", "Avg Pts Gained"]
        athlete_avg["Series"] = "Athlete"

        if not bib_f.empty:
            peer_avg = bib_f.groupby("discipline")["peer_avg_sg"].mean().reset_index()
            peer_avg.columns = ["Discipline", "Avg Pts Gained"]
            peer_avg["Series"] = "±5 Bibs Avg"
            avg_combined = pd.concat([athlete_avg, peer_avg], ignore_index=True)
        else:
            avg_combined = athlete_avg

        fig_avg = go.Figure()
        for series, color in [("Athlete", "#1f77b4"), ("±5 Bibs Avg", "#ff7f0e")]:
            sub_avg = avg_combined[avg_combined["Series"] == series]
            if sub_avg.empty:
                continue
            fig_avg.add_trace(go.Bar(
                x=sub_avg["Discipline"],
                y=sub_avg["Avg Pts Gained"],
                name=series,
                marker_color=color,
                text=sub_avg["Avg Pts Gained"].round(1),
                textposition="outside",
            ))
        fig_avg.add_hline(y=0, line_dash="dot", line_color="gray")
        fig_avg.update_layout(
            height=300, margin=dict(t=20, b=0),
            barmode="group",
            yaxis_title="Avg Points Gained",
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_avg, use_container_width=True)

        # Recent results table
        st.markdown("---")
        st.subheader("All Results")
        st.caption(
            "Full career race history. "
            "Pts Gained = how many FIS points ahead or behind the field average the athlete finished. "
            "±5 Bibs Avg = average strokes gained by athletes who started within 5 bibs in that same race. "
            "Z-Score = performance vs field expressed in standard deviations."
        )
        if not bib_f.empty:
            display = sg_f.merge(
                bib_f[["race_id", "peer_avg_sg"]],
                on="race_id",
                how="left",
            )[["date", "discipline", "location", "fis_points", "points_gained", "peer_avg_sg", "race_z_score"]].copy()
            display["peer_avg_sg"] = display["peer_avg_sg"].round(1)
        else:
            display = sg_f[["date", "discipline", "location", "fis_points", "points_gained", "race_z_score"]].copy()
            display.insert(5, "peer_avg_sg", None)
        display["date"]          = display["date"].dt.strftime("%Y-%m-%d")
        display["points_gained"] = display["points_gained"].round(1)
        display["race_z_score"]  = display["race_z_score"].round(3)
        display.columns = ["Date", "Disc", "Location", "FIS Pts", "Pts Gained", "Peer Avg SG", "Z-Score"]
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("No strokes gained data available.")


# ===========================================================================
# Top Performances
# ===========================================================================
elif section == "Top Performances":

    st.subheader("Top Performances")
    st.caption(
        "Best career results ranked by FIS points — lower is better. "
        "Pts Gained shows how far ahead of the field average the athlete finished in that race."
    )

    if not top_f.empty:
        top3 = top_f.head(3)
        card_cols = st.columns(min(3, len(top3)))
        for col, label, (_, row) in zip(card_cols, ["1st", "2nd", "3rd"], top3.iterrows()):
            with col:
                with st.container(border=True):
                    st.markdown(f"**{label} — {row.get('location', 'N/A')}**")
                    st.markdown(f"*{row['date'].strftime('%Y-%m-%d')} · {row['discipline']}*")
                    st.metric("FIS Points", f"{row['fis_points']:.1f}")
                    if pd.notna(row.get("race_z_score")):
                        st.metric("Z-Score", f"{row['race_z_score']:.3f}")
                    if pd.notna(row.get("points_gained")):
                        st.metric("Pts Gained", f"{row['points_gained']:.1f}")

        st.markdown("---")
        st.subheader("Full Top 10")
        st.caption("Top 10 career performances by FIS points.")
        display = top_f.head(10)[["date", "discipline", "location", "fis_points", "points_gained", "race_z_score"]].copy()
        display["date"]          = display["date"].dt.strftime("%Y-%m-%d")
        display["fis_points"]    = display["fis_points"].round(1)
        display["points_gained"] = display["points_gained"].round(1)
        display["race_z_score"]  = display["race_z_score"].round(3)
        display.columns = ["Date", "Disc", "Location", "FIS Pts", "Pts Gained", "Z-Score"]
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("Best Race Per Year")
        st.caption("Single best result (by FIS points) each season, per discipline.")
        best_yearly = (
            top_f.assign(year=top_f["date"].dt.year)
            .sort_values("fis_points")
            .groupby(["year", "discipline"])
            .first()
            .reset_index()
        )
        best_display = best_yearly[["year", "discipline", "location", "fis_points", "race_z_score"]].copy()
        best_display["fis_points"]   = best_display["fis_points"].round(1)
        best_display["race_z_score"] = best_display["race_z_score"].round(3)
        best_display.columns = ["Year", "Disc", "Location", "FIS Pts", "Z-Score"]
        st.dataframe(
            best_display.sort_values(["Year", "Disc"], ascending=[False, True]),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("No top performance data available.")


# ===========================================================================
# Best Hills
# ===========================================================================
elif section == "Best Hills":

    st.subheader("Best Hills")
    st.caption(
        "Venues ranked by this athlete's historical performance. "
        "Avg Z-Score measures how consistently the athlete beats the field at each location — "
        "positive = outperforms, negative = underperforms. "
        "Best FIS is the athlete's single best result ever at that venue. "
        "Only venues with at least 2 races are shown to keep results meaningful."
    )

    if not locs_f.empty:
        # Require at least 2 races for reliability
        locs_min2 = locs_f[locs_f["race_count"] >= 2].copy()

        if locs_min2.empty:
            st.info("Not enough repeated visits to any venue yet (need 2+ races per location).")
        else:
            # --- Bar chart: top/bottom venues by avg Z-score ---
            for disc in sorted(locs_min2["discipline"].unique()):
                disc_data = locs_min2[locs_min2["discipline"] == disc].sort_values(
                    "avg_z_score", ascending=False
                )
                if disc_data.empty:
                    continue

                st.markdown(f"**{disc}**")

                bar_colors = [
                    "#1f77b4" if v >= 0 else "#d62728"
                    for v in disc_data["avg_z_score"]
                ]
                fig_hills = go.Figure(go.Bar(
                    x=disc_data["location"],
                    y=disc_data["avg_z_score"],
                    marker_color=bar_colors,
                    text=disc_data["avg_z_score"].round(3),
                    textposition="outside",
                    customdata=disc_data[["race_count", "best_fis_points", "avg_pts_gained"]].values,
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        "Avg Z-Score: %{y:.3f}<br>"
                        "Races: %{customdata[0]}<br>"
                        "Best FIS: %{customdata[1]:.1f}<br>"
                        "Avg Pts Gained: %{customdata[2]:.1f}<extra></extra>"
                    ),
                ))
                fig_hills.add_hline(y=0, line_dash="dot", line_color="gray")
                fig_hills.update_layout(
                    height=max(300, len(disc_data) * 40),
                    margin=dict(t=20, b=20),
                    yaxis_title="Avg Z-Score vs Field",
                    xaxis_title="",
                    showlegend=False,
                    xaxis=dict(tickangle=-30),
                )
                st.plotly_chart(fig_hills, use_container_width=True)

            # --- Full table ---
            st.markdown("---")
            st.subheader("Full Venue Breakdown")
            st.caption(
                "All venues with 2+ starts, sorted by Avg Z-Score. "
                "Avg Pts Gained = average strokes gained vs the field at that location. "
                "Last Raced = most recent visit."
            )
            table = locs_min2.sort_values("avg_z_score", ascending=False).copy()
            table["last_raced"]       = table["last_raced"].dt.strftime("%Y-%m-%d")
            table["avg_fis_points"]   = table["avg_fis_points"].round(1)
            table["best_fis_points"]  = table["best_fis_points"].round(1)
            table["avg_pts_gained"]   = table["avg_pts_gained"].round(1)
            table["avg_z_score"]      = table["avg_z_score"].round(3)
            table = table[[
                "location", "discipline", "race_count",
                "avg_z_score", "avg_pts_gained",
                "avg_fis_points", "best_fis_points", "last_raced",
            ]]
            table.columns = [
                "Location", "Disc", "Races",
                "Avg Z-Score", "Avg Pts Gained",
                "Avg FIS", "Best FIS", "Last Raced",
            ]
            st.dataframe(table, use_container_width=True, hide_index=True)

    else:
        st.info("No venue data available.")
