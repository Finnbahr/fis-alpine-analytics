"""
Alpine Analytics — Race Simulator

Monte Carlo race predictor. User selects a venue + discipline, uploads a start
list (CSV: Bib, FIS_Code), and the simulator samples each athlete's performance
from their historical Z-score distribution at that venue, then ranks them across
10,000 simulated races to produce an expected position and confidence range.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import io
from database import query


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Race Simulator — Alpine Analytics",
    layout="wide",
)

st.title("Race Simulator")

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
        <p>Full backtesting and model validation are in progress.<br>Check back soon.</p>
    </div>
</div>
""", unsafe_allow_html=True)

st.caption(
    "Upload a start list and the simulator builds a probabilistic finish-order prediction "
    "using each athlete's historical Z-score distribution at this venue. "
    "10,000 Monte Carlo draws produce an expected position and 80% confidence interval."
)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_venue_options() -> pd.DataFrame:
    """
    Return venue + discipline combos that have at least 5 distinct races in
    the race_z_score table so the simulation has something to draw from.
    """
    return query("""
        SELECT
            rd.location,
            rd.discipline,
            COUNT(DISTINCT rz.race_id) AS race_count
        FROM race_aggregate.race_z_score rz
        JOIN raw.race_details rd ON rd.race_id = rz.race_id
        WHERE rd.location IS NOT NULL AND rd.location != ''
          AND rd.discipline IS NOT NULL AND rd.discipline != ''
        GROUP BY rd.location, rd.discipline
        HAVING COUNT(DISTINCT rz.race_id) >= 5
        ORDER BY rd.location, rd.discipline
    """)


@st.cache_data(ttl=604800)
def load_z_scores_at_venue(location: str, discipline: str) -> pd.DataFrame:
    """
    Return all (fis_code, name, race_z_score) rows for a venue + discipline.
    Used to build per-athlete distributions.
    """
    return query("""
        SELECT
            rz.fis_code,
            rz.name,
            rz.race_z_score
        FROM race_aggregate.race_z_score rz
        JOIN raw.race_details rd ON rd.race_id = rz.race_id
        WHERE rd.location = :loc
          AND rd.discipline = :disc
          AND rz.race_z_score IS NOT NULL
    """, {"loc": location, "disc": discipline})


@st.cache_data(ttl=604800)
def load_similar_venue_z_scores(location: str, discipline: str) -> pd.DataFrame:
    """
    Return Z-scores from the top-3 most similar venues (from course_aggregate.similar_courses),
    to supplement venue-specific history. Returns empty DataFrame if no similar
    courses are available.
    """
    similar = query("""
        SELECT similar_location
        FROM course_aggregate.similar_courses
        WHERE ref_location = :loc
          AND ref_discipline = :disc
        ORDER BY similarity_score ASC
        LIMIT 3
    """, {"loc": location, "disc": discipline})

    if similar.empty:
        return pd.DataFrame(columns=["fis_code", "name", "race_z_score"])

    sim_locs = similar["similar_location"].tolist()
    placeholders = ", ".join([f":loc{i}" for i in range(len(sim_locs))])
    params = {"disc": discipline}
    for i, loc in enumerate(sim_locs):
        params[f"loc{i}"] = loc

    return query(f"""
        SELECT
            rz.fis_code,
            rz.name,
            rz.race_z_score
        FROM race_aggregate.race_z_score rz
        JOIN raw.race_details rd ON rd.race_id = rz.race_id
        WHERE rd.location IN ({placeholders})
          AND rd.discipline = :disc
          AND rz.race_z_score IS NOT NULL
    """, params)


@st.cache_data(ttl=604800)
def load_career_z_stats(discipline: str) -> pd.DataFrame:
    """
    Career mean + std Z-score per athlete at a discipline — used as fallback when
    an athlete has fewer than MIN_VENUE_RACES races at the selected venue.
    """
    return query("""
        SELECT
            fis_code,
            mean_race_z_score AS mu_z,
            COALESCE(std_race_z_score, 0.5) AS sigma_z
        FROM athlete_aggregate.basic_athlete_info_career
        WHERE discipline = :disc
          AND mean_race_z_score IS NOT NULL
          AND race_count >= 3
    """, {"disc": discipline})


@st.cache_data(ttl=604800)
def load_dnf_rates(discipline: str) -> pd.DataFrame:
    """
    Career DNF rate per athlete per discipline.
    """
    return query("""
        SELECT
            fis_code,
            dnf_rate
        FROM athlete_aggregate.performance_consistency_career
        WHERE discipline = :disc
          AND dnf_rate IS NOT NULL
    """, {"disc": discipline})


@st.cache_data(ttl=604800)
def lookup_athletes_by_name(names: tuple) -> pd.DataFrame:
    """
    Fuzzy-ish lookup: return the best fis_code for a list of names using
    ILIKE matching on the hot_streak table.
    """
    if not names:
        return pd.DataFrame(columns=["search_name", "fis_code", "name"])
    rows = []
    for n in names:
        result = query("""
            SELECT DISTINCT ON (fis_code) fis_code, name
            FROM athlete_aggregate.hot_streak
            WHERE LOWER(name) LIKE LOWER(:pattern)
            LIMIT 1
        """, {"pattern": f"%{n.strip()}%"})
        if not result.empty:
            rows.append({"search_name": n, "fis_code": result.iloc[0]["fis_code"],
                          "name": result.iloc[0]["name"]})
        else:
            rows.append({"search_name": n, "fis_code": None, "name": n})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Simulation engine
# ---------------------------------------------------------------------------

MIN_VENUE_RACES = 3   # minimum races at venue to use venue-specific stats
N_SIMS = 10_000       # number of Monte Carlo draws
MIN_SIGMA = 0.30      # floor for σ to prevent zero-variance athletes


def build_athlete_params(
    start_list: pd.DataFrame,     # columns: bib, fis_code, name
    venue_z: pd.DataFrame,        # columns: fis_code, name, race_z_score  (weight 1.0)
    similar_z: pd.DataFrame,      # columns: fis_code, name, race_z_score  (weight 0.5)
    career_stats: pd.DataFrame,   # columns: fis_code, mu_z, sigma_z
    dnf_rates: pd.DataFrame,      # columns: fis_code, dnf_rate
) -> pd.DataFrame:
    """
    For each athlete in the start list, compute (mu_z, sigma_z, dnf_rate, data_source).

    Priority:
      1. Venue-specific stats (>= MIN_VENUE_RACES races at exact venue)
      2. Venue + similar-venue stats combined (if total >= MIN_VENUE_RACES)
      3. Career-level stats for the discipline
      4. Default: mu=0, sigma=1 (unknown athlete)
    """
    results = []

    career_lookup = career_stats.set_index("fis_code")[["mu_z", "sigma_z"]].to_dict("index") if not career_stats.empty else {}
    dnf_lookup = dnf_rates.set_index("fis_code")["dnf_rate"].to_dict() if not dnf_rates.empty else {}

    venue_grp = venue_z.groupby("fis_code")["race_z_score"] if not venue_z.empty else {}
    similar_grp = similar_z.groupby("fis_code")["race_z_score"] if not similar_z.empty else {}

    for _, row in start_list.iterrows():
        fis = row["fis_code"]
        athlete_name = row["name"]

        # Collect venue-specific scores (weight 1.0)
        v_scores = venue_grp.get_group(fis).tolist() if (not venue_z.empty and fis in venue_z["fis_code"].values) else []
        # Collect similar-venue scores (weight 0.5 → repeat each score 0.5x via sampling)
        s_scores_raw = similar_grp.get_group(fis).tolist() if (not similar_z.empty and fis in similar_z["fis_code"].values) else []
        # Combine: use venue scores + half the similar scores
        combined = v_scores + [s for s in s_scores_raw]  # include all; more weight via count

        if len(v_scores) >= MIN_VENUE_RACES:
            mu_z = float(np.mean(v_scores))
            sigma_z = max(float(np.std(v_scores, ddof=1)) if len(v_scores) > 1 else MIN_SIGMA, MIN_SIGMA)
            source = "Venue"
        elif len(combined) >= MIN_VENUE_RACES:
            mu_z = float(np.mean(combined))
            sigma_z = max(float(np.std(combined, ddof=1)) if len(combined) > 1 else MIN_SIGMA, MIN_SIGMA)
            source = "Venue + Similar"
        elif fis in career_lookup:
            mu_z = career_lookup[fis]["mu_z"]
            sigma_z = max(career_lookup[fis]["sigma_z"], MIN_SIGMA)
            source = "Career avg"
        else:
            mu_z = 0.0
            sigma_z = 1.0
            source = "No data"

        dnf_rate = dnf_lookup.get(fis, 0.05)  # default 5% if unknown

        results.append({
            "bib": row["bib"],
            "fis_code": fis,
            "name": athlete_name,
            "mu_z": mu_z,
            "sigma_z": sigma_z,
            "dnf_rate": dnf_rate,
            "data_source": source,
        })

    return pd.DataFrame(results)


def run_simulation(params: pd.DataFrame, n_sims: int = N_SIMS) -> pd.DataFrame:
    """
    Monte Carlo race simulation.

    params: output of build_athlete_params() — one row per athlete.

    Returns DataFrame with columns:
        bib, fis_code, name, data_source,
        mean_pos, p10, p90, dnf_pct
    sorted by mean_pos ascending.
    """
    if params.empty:
        return pd.DataFrame()

    n = len(params)
    rng = np.random.default_rng(42)

    mu = params["mu_z"].values[:, None]        # (n, 1)
    sigma = params["sigma_z"].values[:, None]  # (n, 1)
    dnf_p = params["dnf_rate"].values[:, None] # (n, 1)

    # Sample Z-scores and DNF outcomes
    z_draws = rng.normal(mu, sigma, size=(n, n_sims))     # (n, n_sims)
    dnf_draws = rng.random(size=(n, n_sims)) < dnf_p      # (n, n_sims) bool

    # DNF athletes get a very low Z (ranks last)
    z_effective = np.where(dnf_draws, -1e9, z_draws)

    # Vectorized ranking: argsort each column descending, then invert permutation
    # sorted_idx[k, j] = index of athlete ranked k-th in sim j (0-indexed position)
    sorted_idx = np.argsort(-z_effective, axis=0)   # (n, n_sims)
    # positions[i, j] = rank (1-based) of athlete i in sim j
    positions = np.argsort(sorted_idx, axis=0) + 1  # (n, n_sims)

    result = params[["bib", "fis_code", "name", "data_source"]].copy()
    result["mean_pos"] = positions.mean(axis=1).round(1)
    result["p10"] = np.percentile(positions, 10, axis=1).astype(int)
    result["p90"] = np.percentile(positions, 90, axis=1).astype(int)
    result["dnf_pct"] = (dnf_draws.mean(axis=1) * 100).round(1)
    result = result.sort_values("mean_pos").reset_index(drop=True)
    result.insert(0, "pred_rank", range(1, len(result) + 1))

    return result


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# --- Sidebar: venue + discipline ---
st.sidebar.header("Race Setup")

venue_df = load_venue_options()
disciplines = sorted(venue_df["discipline"].unique().tolist())
sel_disc = st.sidebar.selectbox("Discipline", disciplines)

venues_for_disc = (
    venue_df[venue_df["discipline"] == sel_disc]
    .sort_values("location")["location"]
    .tolist()
)
sel_venue = st.sidebar.selectbox("Venue", venues_for_disc)

use_similar = st.sidebar.checkbox("Include similar venues", value=True,
    help="Augments venue-specific data with Z-scores from the top-3 most similar courses. "
         "Helps athletes with fewer appearances at this venue.")

st.sidebar.markdown("---")
st.sidebar.caption(
    f"**{N_SIMS:,} simulations** per run.  \n"
    "Each athlete's finish is sampled from their historical Z-score distribution at "
    "this venue. DNF probability is drawn from their career DNF rate for this discipline."
)

# --- Main: start list input ---
st.subheader("Start List")

col_info, col_tmpl = st.columns([3, 1])
with col_info:
    st.markdown(
        "Upload a CSV with **Bib** and **FIS_Code** columns (integer FIS licence numbers). "
        "Bib order is displayed in results but does not affect the simulation — "
        "each athlete's history determines their predicted position."
    )
with col_tmpl:
    template_csv = "Bib,FIS_Code\n1,123456\n2,234567\n3,345678\n"
    st.download_button(
        "Download template",
        data=template_csv,
        file_name="start_list_template.csv",
        mime="text/csv",
    )

uploaded = st.file_uploader(
    "Upload start list CSV",
    type=["csv"],
    help="Required columns: Bib (integer), FIS_Code (integer). "
         "An optional Name column will override FIS-code lookups for display.",
)

start_list = None
parse_error = None

if uploaded is not None:
    try:
        raw_df = pd.read_csv(uploaded)
        raw_df.columns = [c.strip().lower().replace(" ", "_") for c in raw_df.columns]

        if "bib" not in raw_df.columns:
            parse_error = "CSV must have a 'Bib' column."
        elif "fis_code" not in raw_df.columns and "name" not in raw_df.columns:
            parse_error = "CSV must have a 'FIS_Code' or 'Name' column."
        else:
            # Normalise bib
            raw_df["bib"] = pd.to_numeric(raw_df["bib"], errors="coerce")
            raw_df = raw_df.dropna(subset=["bib"])
            raw_df["bib"] = raw_df["bib"].astype(int)

            if "fis_code" in raw_df.columns:
                raw_df["fis_code"] = pd.to_numeric(raw_df["fis_code"], errors="coerce")
                raw_df = raw_df.dropna(subset=["fis_code"])
                raw_df["fis_code"] = raw_df["fis_code"].astype(int)
                if "name" not in raw_df.columns:
                    raw_df["name"] = raw_df["fis_code"].astype(str)
            else:
                # Name-based lookup
                names = tuple(raw_df["name"].dropna().unique().tolist())
                lookup = lookup_athletes_by_name(names)
                raw_df = raw_df.merge(
                    lookup.rename(columns={"search_name": "name", "fis_code": "fis_code_looked_up"}),
                    on="name", how="left"
                )
                raw_df["fis_code"] = pd.to_numeric(raw_df.get("fis_code_looked_up"), errors="coerce")
                raw_df = raw_df.dropna(subset=["fis_code"])
                raw_df["fis_code"] = raw_df["fis_code"].astype(int)

            # Name column: prefer uploaded name, fall back to fis_code string
            if "name" not in raw_df.columns:
                raw_df["name"] = raw_df["fis_code"].astype(str)

            start_list = raw_df[["bib", "fis_code", "name"]].drop_duplicates(subset=["bib"]).sort_values("bib")

    except Exception as e:
        parse_error = f"Could not parse CSV: {e}"

if parse_error:
    st.error(parse_error)

if start_list is not None:
    st.markdown(f"**{len(start_list)} athletes loaded** — {sel_disc} at {sel_venue}")

    with st.expander("Preview start list"):
        st.dataframe(start_list, use_container_width=True, hide_index=True)

    simulate = st.button("Run Simulation", type="primary")

    if simulate:
        with st.spinner("Loading historical data and running simulation..."):
            # Load venue Z-scores
            venue_z = load_z_scores_at_venue(sel_venue, sel_disc)

            # Optionally load similar-venue data
            if use_similar:
                similar_z = load_similar_venue_z_scores(sel_venue, sel_disc)
            else:
                similar_z = pd.DataFrame(columns=["fis_code", "name", "race_z_score"])

            # Load career fallbacks
            career_stats = load_career_z_stats(sel_disc)
            dnf_rates = load_dnf_rates(sel_disc)

            # Build per-athlete distribution params
            params = build_athlete_params(start_list, venue_z, similar_z, career_stats, dnf_rates)

            # Run Monte Carlo
            results = run_simulation(params)

        # --- Results ---
        st.markdown("---")
        st.subheader("Simulation Results")

        n_no_data = (results["data_source"] == "No data").sum()
        n_venue = (results["data_source"] == "Venue").sum()
        n_career = (results["data_source"] == "Career avg").sum()

        info_parts = []
        if n_venue:
            info_parts.append(f"{n_venue} athletes with venue-specific history")
        if (results["data_source"] == "Venue + Similar").sum():
            info_parts.append(f"{(results['data_source'] == 'Venue + Similar').sum()} with nearby venue data")
        if n_career:
            info_parts.append(f"{n_career} using career average (limited venue data)")
        if n_no_data:
            info_parts.append(f"{n_no_data} with no historical data (shown with wide uncertainty)")
        if info_parts:
            st.info("  |  ".join(info_parts))

        # Table
        display = results.copy()
        display.columns = [
            "Pred. Rank", "Bib", "FIS Code", "Athlete", "Data Source",
            "Expected Pos", "P10", "P90", "DNF %"
        ]
        display["Range"] = display["P10"].astype(str) + " – " + display["P90"].astype(str)
        st.dataframe(
            display[["Pred. Rank", "Bib", "Athlete", "Expected Pos", "Range", "DNF %", "Data Source"]],
            use_container_width=True,
            hide_index=True,
        )

        # --- Chart: horizontal position-range plot ---
        st.markdown("#### Predicted Finish Position — 80% Confidence Interval")
        st.caption(
            "Bar spans P10–P90 across 10,000 simulations. Dot marks expected (mean) position. "
            "Lower = better (position 1 = predicted winner)."
        )

        fig = go.Figure()

        # Sorted by mean_pos ascending = best predicted finisher at top of chart
        chart_df = results.sort_values("mean_pos", ascending=False)  # ascending=False → best at top of horizontal bar

        # P10-P90 range bars
        fig.add_trace(go.Bar(
            y=chart_df["name"],
            x=chart_df["p90"] - chart_df["p10"],
            base=chart_df["p10"],
            orientation="h",
            marker_color="steelblue",
            opacity=0.45,
            name="80% range (P10–P90)",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "P10–P90: %{base:.0f} – %{x:.0f}<br>"
                "<extra></extra>"
            ),
        ))

        # Expected position dot
        fig.add_trace(go.Scatter(
            y=chart_df["name"],
            x=chart_df["mean_pos"],
            mode="markers",
            marker=dict(color="navy", size=8, symbol="circle"),
            name="Expected position",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Expected: %{x:.1f}<br>"
                "<extra></extra>"
            ),
        ))

        n_athletes = len(chart_df)
        fig.update_layout(
            xaxis=dict(
                title="Finish Position",
                range=[0.5, n_athletes + 0.5],
                tickmode="linear",
                tick0=1,
                dtick=max(1, n_athletes // 10),
            ),
            yaxis=dict(title="", automargin=True),
            legend=dict(orientation="h", y=-0.12),
            height=max(350, 22 * n_athletes),
            margin=dict(l=160, r=40, t=30, b=60),
            plot_bgcolor="white",
            paper_bgcolor="white",
        )
        fig.update_xaxes(showgrid=True, gridcolor="#eee")
        fig.update_yaxes(showgrid=False)

        st.plotly_chart(fig, use_container_width=True)

        # DNF probability chart (only if any meaningful DNF risk)
        if results["dnf_pct"].max() >= 2.0:
            st.markdown("#### DNF Probability by Athlete")
            dnf_chart = results.sort_values("dnf_pct", ascending=True)
            fig2 = go.Figure(go.Bar(
                y=dnf_chart["name"],
                x=dnf_chart["dnf_pct"],
                orientation="h",
                marker_color="tomato",
                opacity=0.75,
                hovertemplate="<b>%{y}</b><br>DNF probability: %{x:.1f}%<extra></extra>",
            ))
            fig2.update_layout(
                xaxis=dict(title="DNF Probability (%)", range=[0, min(100, dnf_chart["dnf_pct"].max() * 1.2)]),
                yaxis=dict(automargin=True),
                height=max(300, 18 * len(dnf_chart)),
                margin=dict(l=160, r=40, t=20, b=40),
                plot_bgcolor="white",
                paper_bgcolor="white",
            )
            fig2.update_xaxes(showgrid=True, gridcolor="#eee")
            fig2.update_yaxes(showgrid=False)
            st.plotly_chart(fig2, use_container_width=True)

else:
    st.info("Select a venue and discipline in the sidebar, then upload a start list CSV to begin.")
