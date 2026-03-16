"""
Alpine Analytics — Race Simulator

Monte Carlo race predictor. User selects discipline, race type, venue, and sex,
uploads a start list (CSV: Bib, FIS_Code), and the simulator runs each athlete's
performance through the full model (form, course traits, bib, momentum, venue, weather)
across 2,000 simulated races to produce win / podium / top-10 probabilities.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import logging
from database import query

import monte_carlo as mc

logging.disable(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Race Simulator — Alpine Analytics",
    layout="wide",
)

st.title("Race Simulator")

with st.expander("How the Simulator Works"):
    st.markdown(
        """
        The Race Simulator uses a Monte Carlo model to estimate the probability of each outcome
        — win, podium, top 10, and DNF — for every athlete in the start list.

        **Building each athlete's profile**

        Every athlete's recent race results are converted into a standardised performance score
        (z-score) relative to the field they competed against. An exponentially weighted mean
        with a discipline-specific decay rate forms the baseline — Slalom form decays faster
        (half-life ~120 days) while Downhill specialist form persists longer (~270 days). That
        baseline is then shrunk toward the field mean using Bayesian shrinkage, so athletes with
        few career starts are not over-rated on thin data.

        Six adjustments are applied on top of that form baseline:

        - **Form trajectory** — a weighted regression over recent results detects improving or
          declining athletes. Applied to Slalom and Giant Slalom where short-term form swings are
          most predictive; capped tighter for GS to avoid over-fitting.
        - **Venue** — the athlete's personal track record at this specific location, shrinkage-
          adjusted by the number of prior starts there. Athletes with limited venue history fall
          back toward their general form.
        - **Bib / start number** — the historical advantage or penalty of drawing a given start
          position at this location, fitted per venue. Capped more tightly for single-run events
          (Super G, Downhill) where bib order is less systematic.
        - **Momentum** — the athlete's most recent result relative to their own form average,
          normalised by career consistency. Captures hot/cold streaks not yet reflected in the
          weighted mean.
        - **Field quality** — a log-ratio correction for the depth of the start list. The same
          absolute form score is worth more against a world-class field than a weaker one.
        - **Weather** — if conditions are entered, each athlete's historical performance in
          similar temperature, cloud cover, and precipitation bins is applied as an adjustment,
          shrunk toward zero when the athlete has limited weather history.

        **Bounce-back signal (Slalom only)**

        When a Slalom athlete's last race was a DNF or DSQ, the model checks their historical
        average performance in the race immediately after a DNF. Athletes with a strong bounce-back
        record get a positive adjustment; athletes who tend to DNF consecutively are penalised.

        **Running the simulation**

        Once each athlete's adjusted mean and personal variance are set, the model runs 2,000
        independent races. In each simulation every athlete draws a performance from their own
        distribution and the field is ranked. For two-run disciplines (Slalom, Giant Slalom),
        Run 1 results are used to reassign Run 2 bibs by FIS rules (top-30 reversed, 31+ in
        order), and DNF probability is split across both runs. The fraction of times each athlete
        finishes 1st, top 3, or top 10 across those 2,000 races becomes their win, podium,
        and top-10 probability.

        **Interpreting the results**

        A 35% win probability means that under similar conditions against a similar field, that
        athlete would be expected to win roughly 35 times in every 100 starts — not that the
        model is wrong when they finish third. All probabilities reflect genuine uncertainty;
        no simulation picks a guaranteed winner because none exist.

        Results are sorted by win probability. The **Breakout Pick** highlights the athlete the
        model rates significantly higher than their start bib suggests.
        """
    )

with st.expander("Model Accuracy — Backtesting Results"):
    st.markdown(
        """
        The model was validated on **310 World Cup races from 2022 onward** — each race simulated
        using only data available before race day, then compared against actual results.
        No future information was used at any point.

        Results by discipline (2,000 simulations per race, combined Men and Women):
        """
    )

    import pandas as _pd
    _bt = _pd.DataFrame({
        "Discipline":      ["Slalom", "Giant Slalom", "Super G", "Downhill"],
        "Races (M+W)":     [90, 79, 66, 75],
        "Spearman Rho":    ["0.612", "0.664", "0.686", "0.684"],
        "Winner %":        ["34%", "46%", "21%", "24%"],
        "Top-3 %":         ["42%", "42%", "38%", "35%"],
        "Avg. Rank Error": ["7.7", "7.3", "7.5", "8.0"],
    })
    st.dataframe(_bt, use_container_width=True, hide_index=True)

    st.markdown(
        """
        **How to read these numbers:**

        - **Spearman Rho** — rank correlation between predicted and actual finishing order among
          finishers (1.0 = perfect, 0 = no relationship). Values of 0.61–0.69 indicate a strong,
          statistically significant relationship.
        - **Winner %** — fraction of races where the model's top-ranked athlete actually won.
          A random pick from a 60-athlete field would win roughly 1.7% of the time.
        - **Top-3 %** — fraction of actual podium athletes captured in the model's predicted top 3.
        - **Avg. Rank Error** — mean positional error across all finishers. Predicting by bib
          order alone averages 9–10 positions of error.

        **FIS and European Cup races**

        The simulator supports all race types via the Race Type selector in the sidebar.
        A cross-level validation study using the companion XGBoost model found that the
        underlying performance signals transfer cleanly to FIS races — Spearman Rho of
        0.88 for Slalom and 0.88 for Giant Slalom across 5,600+ FIS races (2022+), with
        lower rank error than at World Cup level due to wider field spread.
        """
    )

# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800)
def load_courses(discipline: str) -> pd.DataFrame:
    """Venues with at least 1 race for this discipline."""
    df = mc.list_courses(discipline=discipline, min_races=1)
    return df.sort_values("location")


@st.cache_data(ttl=604800)
def lookup_athletes_by_name(names: tuple) -> pd.DataFrame:
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
            rows.append({"search_name": n,
                         "fis_code": result.iloc[0]["fis_code"],
                         "name":     result.iloc[0]["name"]})
        else:
            rows.append({"search_name": n, "fis_code": None, "name": n})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Sidebar — race setup
# ---------------------------------------------------------------------------

st.sidebar.header("Race Setup")

DISCIPLINES = ["Slalom", "Giant Slalom", "Super G", "Downhill", "Alpine Combined"]
RACE_TYPES  = [
    "World Cup",
    "European Cup",
    "FIS",
    "Nor-Am Cup",
    "CIT",
    "National Championships",
    "National Junior Race",
    "National Junior Championships",
    "Entry League FIS",
    "Far East Cup",
    "South American Cup",
    "Australian New Zealand Cup",
    "University",
    "FIS Junior World Ski Championships",
    "FIS Qualification",
]

sel_disc      = st.sidebar.selectbox("Discipline", DISCIPLINES)
sel_race_type = st.sidebar.selectbox("Race Type", RACE_TYPES)

# Venue — driven by discipline
courses_df = load_courses(sel_disc)
venue_names = courses_df["location"].drop_duplicates().tolist() if not courses_df.empty else []

if venue_names:
    sel_venue = st.sidebar.selectbox("Venue", venue_names)

    # All homologation numbers for this venue + discipline
    venue_rows = courses_df[courses_df["location"] == sel_venue].copy()
    homos = venue_rows["homologation_number"].dropna().astype(str).unique().tolist()

    if len(homos) > 1:
        # Show race count alongside each number so user can pick the right course
        homo_labels = []
        for h in homos:
            rc = venue_rows[venue_rows["homologation_number"].astype(str) == h]["race_count"].sum()
            homo_labels.append(f"{h}  ({rc} races)")
        sel_homo_label = st.sidebar.selectbox(
            "Homologation Number",
            homo_labels,
            help="Multiple courses exist at this venue. Select the specific homologation number to use.",
        )
        sel_homologation = sel_homo_label.split("  (")[0]
    elif len(homos) == 1:
        sel_homologation = homos[0]
        st.sidebar.caption(f"Homologation: {sel_homologation}")
    else:
        sel_homologation = None
else:
    st.sidebar.warning("No venues found for this discipline.")
    sel_venue        = None
    sel_homologation = None

sex_label = st.sidebar.radio("Sex", ["Men (M)", "Women (F)"])
sex_code  = "Men's" if sex_label.startswith("Men") else "Women's"

st.sidebar.markdown("---")

setter_name = st.sidebar.text_input(
    "Course Setter (optional)",
    placeholder="e.g. Ferrero Roberto",
    help="Used as a fallback signal at venues with limited homologation data.",
)
setter_name = setter_name.strip() or None

# Weather
with st.sidebar.expander("Weather Conditions (optional)"):
    use_weather = st.checkbox("Include weather in simulation", value=False)
    if use_weather:
        w_temp  = st.slider("Air Temperature (°C)", -20, 10, -5)
        w_cloud = st.slider("Cloud Cover (%)", 0, 100, 30)
        w_precip = st.number_input("Precipitation last 24h (mm)", 0.0, 50.0, 0.0, step=0.5)
        weather_dict = {
            "air_temp_c":     float(w_temp),
            "cloud_cover":    float(w_cloud),
            "precip_24h_mm":  float(w_precip),
        }
    else:
        weather_dict = None

st.sidebar.markdown("---")
st.sidebar.caption(
    "**2,000 simulations** per run.  \n"
    "Each athlete's predicted performance is drawn from their historical z-score "
    "distribution, adjusted for course traits, bib order, recent form, venue history, "
    "and (optionally) weather conditions."
)

# ---------------------------------------------------------------------------
# Main — start list input
# ---------------------------------------------------------------------------

st.subheader("Start List")

col_info, col_tmpl = st.columns([3, 1])
with col_info:
    st.markdown(
        "Upload a CSV with **Bib** and **FIS_Code** columns. "
        "An optional **Name** column overrides FIS-lookup display names. "
        "If only names are provided (no FIS codes), the simulator will attempt to match them."
    )
with col_tmpl:
    template_csv = "Bib,FIS_Code,Name\n1,206355,ODERMATT Marco\n2,512182,MEILLARD Loic\n3,422304,KRISTOFFERSEN Henrik\n"
    st.download_button(
        "Download template",
        data=template_csv,
        file_name="start_list_template.csv",
        mime="text/csv",
    )

uploaded = st.file_uploader(
    "Upload start list CSV",
    type=["csv"],
    help="Required columns: Bib (integer), FIS_Code (integer). Name column is optional.",
)

start_list  = None
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
                names   = tuple(raw_df["name"].dropna().unique().tolist())
                lookup  = lookup_athletes_by_name(names)
                raw_df  = raw_df.merge(
                    lookup.rename(columns={"search_name": "name",
                                           "fis_code": "fis_code_looked_up"}),
                    on="name", how="left",
                )
                raw_df["fis_code"] = pd.to_numeric(
                    raw_df.get("fis_code_looked_up"), errors="coerce"
                )
                raw_df = raw_df.dropna(subset=["fis_code"])
                raw_df["fis_code"] = raw_df["fis_code"].astype(int)

            if "name" not in raw_df.columns:
                raw_df["name"] = raw_df["fis_code"].astype(str)

            start_list = (
                raw_df[["bib", "fis_code", "name"]]
                .drop_duplicates(subset=["bib"])
                .sort_values("bib")
                .reset_index(drop=True)
            )

    except Exception as e:
        parse_error = f"Could not parse CSV: {e}"

if parse_error:
    st.error(parse_error)

if start_list is not None:
    st.markdown(
        f"**{len(start_list)} athletes loaded** — "
        f"{sel_disc} · {sel_race_type} · {sel_venue or '(no venue)'} · {sex_label}"
    )

    with st.expander("Preview start list"):
        st.dataframe(start_list, use_container_width=True, hide_index=True)

    can_run = sel_venue is not None
    simulate = st.button(
        "Run Simulation",
        type="primary",
        disabled=not can_run,
    )

    if not can_run:
        st.warning("Select a venue before running.")

    if simulate and can_run:
        # Ensure fis_code is string (required by monte_carlo engine)
        sim_list = start_list.copy()
        sim_list["fis_code"] = sim_list["fis_code"].astype(str)

        with st.spinner(f"Running 2,000 simulations for {len(sim_list)} athletes..."):
            try:
                predictions = mc.run_simulation(
                    start_list        = sim_list,
                    discipline        = sel_disc,
                    race_type         = sel_race_type,
                    location          = sel_venue,
                    homologation_number = sel_homologation,
                    setter_name       = setter_name,
                    n_sims            = 2_000,
                    random_seed       = 42,
                    sex               = sex_code,
                    weather_conditions = weather_dict,
                )
            except Exception as e:
                st.error(f"Simulation error: {e}")
                predictions = None

        if predictions is not None and not predictions.empty:
            # ----------------------------------------------------------------
            # Summary metrics
            # ----------------------------------------------------------------
            st.markdown("---")
            st.subheader("Simulation Results")

            winner_row   = predictions.iloc[0]
            # Last names only so they fit in the metric card
            top3_lastnames = " / ".join(n.split()[-1] for n in predictions.head(3)["name"].tolist())

            # Breakout Pick: athlete with the biggest gap between start bib rank
            # and predicted rank (i.e. biggest outsider the model likes)
            _pr = predictions.reset_index(drop=True)
            _pr["_pred_rank"] = range(1, len(_pr) + 1)
            _pr["_bib_rank"]  = _pr["bib"].rank(method="min").astype(int)
            _pr["_improvement"] = _pr["_bib_rank"] - _pr["_pred_rank"]
            _outsiders = _pr[_pr["_bib_rank"] > 5]
            if not _outsiders.empty and _outsiders["_improvement"].max() > 0:
                _breakout = _outsiders.nlargest(1, "_improvement").iloc[0]
                breakout_label = f"Bib {int(_breakout['bib'])} → Pred. {int(_breakout['_pred_rank'])}"
                breakout_name  = _breakout["name"]
            else:
                _breakout      = predictions.iloc[1]
                breakout_label = f"{_breakout['p_win']:.1f}% win probability"
                breakout_name  = _breakout["name"]

            m1, m2, m3 = st.columns(3)
            m1.metric(
                "Predicted Winner",
                winner_row["name"],
                f"{min(winner_row['p_win'], 99.9):.1f}% win probability",
            )
            m2.metric("Favorites", top3_lastnames)
            m3.metric("Breakout Pick", breakout_name, breakout_label)

            # ----------------------------------------------------------------
            # Results table
            # ----------------------------------------------------------------
            display = predictions.copy()
            display.insert(0, "#", range(1, len(display) + 1))

            display["P(Win)"]    = display["p_win"].clip(upper=99.9).round(1).astype(str) + "%"
            display["P(Podium)"] = display["p_podium"].clip(upper=99.9).round(1).astype(str) + "%"
            display["P(Top 10)"] = display["p_top10"].clip(upper=99.9).round(1).astype(str) + "%"
            display["P(DNF)"]    = display["p_dnf"].clip(upper=99.9).round(1).astype(str) + "%"

            table_cols = ["#", "bib", "name", "P(Win)", "P(Podium)", "P(Top 10)", "P(DNF)", "expected_rank"]
            rename_map = {"bib": "Bib", "name": "Athlete", "expected_rank": "Exp. Rank"}

            st.dataframe(
                display[table_cols].rename(columns=rename_map),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Pred. Rakn": st.column_config.NumberColumn("Pred.Rank",         help="Predicted rank — sorted by win probability"),
                    "Bib":        st.column_config.NumberColumn("Bib",       help="Start bib number"),
                    "Athlete":    st.column_config.TextColumn("Athlete"),
                    "P(Win)":     st.column_config.TextColumn("P(Win)",      help="Probability of winning the race outright"),
                    "P(Podium)":  st.column_config.TextColumn("P(Podium)",   help="Probability of finishing in the top 3"),
                    "P(Top 10)":  st.column_config.TextColumn("P(Top 10)",   help="Probability of finishing in the top 10"),
                    "P(DNF)":     st.column_config.TextColumn("P(DNF)",      help="Probability of not finishing (DNF / DSQ)"),
                    "Exp. Rank":  st.column_config.NumberColumn("Exp. Rank", help="Average simulated finishing position across all 2,000 races — lower is better", format="%.1f"),
                },
            )

            # ----------------------------------------------------------------
            # Win probability chart (top 15)
            # ----------------------------------------------------------------
            st.markdown("#### Win Probability — Top 15")

            chart_df = predictions.head(15).sort_values("p_win", ascending=True)

            fig = go.Figure()
            fig.add_trace(go.Bar(
                y     = chart_df["name"],
                x     = chart_df["p_podium"],
                name  = "P(Podium)",
                orientation = "h",
                marker_color = "steelblue",
                opacity = 0.45,
                hovertemplate = "<b>%{y}</b><br>P(Podium): %{x:.1f}%<extra></extra>",
            ))
            fig.add_trace(go.Bar(
                y     = chart_df["name"],
                x     = chart_df["p_win"],
                name  = "P(Win)",
                orientation = "h",
                marker_color = "navy",
                opacity = 0.85,
                hovertemplate = "<b>%{y}</b><br>P(Win): %{x:.1f}%<extra></extra>",
            ))
            fig.update_layout(
                barmode  = "overlay",
                xaxis    = dict(title="Probability (%)", range=[0, min(100, chart_df["p_podium"].max() * 1.3)]),
                yaxis    = dict(title="", automargin=True),
                legend   = dict(orientation="h", y=-0.15),
                height   = max(320, 28 * len(chart_df)),
                margin   = dict(l=170, r=40, t=20, b=60),
                plot_bgcolor  = "white",
                paper_bgcolor = "white",
            )
            fig.update_xaxes(showgrid=True, gridcolor="#eee")
            fig.update_yaxes(showgrid=False)
            st.plotly_chart(fig, use_container_width=True)

            # ----------------------------------------------------------------
            # DNF chart (only if any athlete >= 2%)
            # ----------------------------------------------------------------
            if predictions["p_dnf"].max() >= 2.0:
                st.markdown("#### DNF Probability by Athlete")
                dnf_chart = predictions.sort_values("p_dnf", ascending=True)
                fig2 = go.Figure(go.Bar(
                    y     = dnf_chart["name"],
                    x     = dnf_chart["p_dnf"],
                    orientation  = "h",
                    marker_color = "tomato",
                    opacity = 0.75,
                    hovertemplate = "<b>%{y}</b><br>DNF probability: %{x:.1f}%<extra></extra>",
                ))
                max_dnf = dnf_chart["p_dnf"].max()
                fig2.update_layout(
                    xaxis  = dict(title="DNF Probability (%)", range=[0, min(100, max_dnf * 1.25)]),
                    yaxis  = dict(automargin=True),
                    height = max(300, 18 * len(dnf_chart)),
                    margin = dict(l=170, r=40, t=20, b=40),
                    plot_bgcolor  = "white",
                    paper_bgcolor = "white",
                )
                fig2.update_xaxes(showgrid=True, gridcolor="#eee")
                fig2.update_yaxes(showgrid=False)
                st.plotly_chart(fig2, use_container_width=True)

            # ----------------------------------------------------------------
            # Factor breakdown
            # ----------------------------------------------------------------
            adj_cols = [
                "name", "base_mean_z", "course_adj", "bib_adj",
                "momentum_adj", "field_adj", "venue_adj", "weather_adj",
                "adjusted_mean_z",
            ]
            available = [c for c in adj_cols if c in predictions.columns]

            if len(available) > 2:
                with st.expander("Factor Breakdown"):
                    st.caption(
                        "All values in z-score units (σ). Positive = faster than average. "
                        "adjusted_mean_z = base_mean_z + sum of all adjustments."
                    )
                    breakdown = predictions[available].copy()
                    rename_breakdown = {
                        "name":           "Athlete",
                        "base_mean_z":    "Base Form",
                        "course_adj":     "Course",
                        "bib_adj":        "Bib",
                        "momentum_adj":   "Momentum",
                        "field_adj":      "Field Quality",
                        "venue_adj":      "Venue",
                        "weather_adj":    "Weather",
                        "adjusted_mean_z": "Adjusted Mean",
                    }
                    breakdown = breakdown.rename(columns=rename_breakdown)
                    # Round numeric columns
                    num_cols = [c for c in breakdown.columns if c != "Athlete"]
                    breakdown[num_cols] = breakdown[num_cols].round(3)
                    st.dataframe(breakdown, use_container_width=True, hide_index=True)

else:
    st.info(
        "Select a discipline, race type, and venue in the sidebar, "
        "then upload a start list CSV to begin."
    )
