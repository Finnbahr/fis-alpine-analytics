"""
Alpine Analytics — XGBoost Race Predictor (v11)

Gradient-boosted ranking model trained on all available World Cup race history.
Upload a start list CSV to get an instant predicted ranking with key
performance indicators per athlete.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import date
import xgboost_model_v11 as v11

st.set_page_config(
    page_title="XGBoost Predictor — Alpine Analytics",
    layout="wide",
)

# ── Password gate ───────────────────────────────────────────────────────────
if not st.session_state.get("xgb_auth"):
    st.title("XGBoost Race Predictor")
    pwd = st.text_input("Password", type="password")
    if pwd == "Plymouthskiing1!":
        st.session_state["xgb_auth"] = True
        st.rerun()
    elif pwd:
        st.error("Incorrect password.")
    st.stop()
# ────────────────────────────────────────────────────────────────────────────

st.title("XGBoost Race Predictor")

with st.expander("How This Model Works"):
    st.markdown(
        """
        The XGBoost predictor uses a gradient-boosted ranking model (XGBRanker) trained on
        every World Cup race in the database. Rather than simulating thousands of outcomes,
        it predicts each athlete's expected performance score directly and ranks the field.

        **Features used per athlete**

        - **EWM form** — exponentially weighted mean z-score (discipline-specific halflife) and short/long variants
        - **Rolling averages** — mean z-score over last 3, 5, and 10 races
        - **Recent wins and podiums** — win rate and podium rate over last 5, 10, and career races
        - **Consistency** — standard deviation of z-scores, CV ratio, consecutive podiums/wins
        - **Form trajectory** — weighted least-squares slope of recent performance (Slalom & GS only)
        - **DNF/DSQ risk** — rolling 5-race DNF rate, career DNF rate, consecutive DNF probability
        - **Bounce-back** — historical z-score in the race after a DNF (Slalom only)
        - **Venue history** — shrinkage-adjusted athlete average at this specific location
        - **Bib position** — start order and its historical performance signal
        - **FIS Points** — rolling 5 and 10-race FIS ranking score
        - **Days since last race** — recency of competition
        - **Weather** — optional athlete weather performance signal (temperature, cloud, precipitation)
        - **Field-relative features** — each stat expressed relative to the current field mean

        **Training**

        The model is trained on the full race database using only information available before
        each race (all rolling features are lagged by one race to prevent look-ahead bias).
        Per-discipline hyperparameters are tuned separately for Slalom, Giant Slalom, Super G,
        and Downhill.

        **Interpreting the output**

        Athletes are ranked by their predicted ranking score (higher = faster). The score is
        a relative ranking signal — the absolute value is less important than the ordering.
        """
    )

with st.expander("Model Accuracy — Backtesting Results"):
    st.markdown(
        """
        Walk-forward validation: trained on pre-2022 history, tested on every race from
        2022 onward. Predictions made using only data available before each race.
        """
    )

    tab_wc, tab_fis = st.tabs(["World Cup", "FIS"])

    with tab_wc:
        st.markdown("**310 World Cup races (2022+), combined Men and Women:**")
        _bt_wc = pd.DataFrame({
            "Discipline":      ["Slalom", "Giant Slalom", "Super G", "Downhill"],
            "Races (M+W)":     [90, 79, 67, 75],
            "Spearman Rho":    ["0.604", "0.682", "0.716", "0.720"],
            "Winner %":        ["35.7%", "43.4%", "31.6%", "27.8%"],
            "Top-3 %":         ["49.3%", "50.9%", "42.0%", "40.8%"],
            "Avg. Rank Error": ["5.0", "4.8", "6.8", "7.6"],
        })
        st.dataframe(_bt_wc, use_container_width=True, hide_index=True)

    with tab_fis:
        st.markdown(
            "**5,600+ FIS races (2022+), trained on FIS history — combined Men and Women:**"
        )
        _bt_fis = pd.DataFrame({
            "Discipline":      ["Slalom", "Giant Slalom", "Super G", "Downhill"],
            "Races (M+W)":     [3090, 2307, 494, 210],
            "Spearman Rho":    ["0.883", "0.880", "0.777", "0.718"],
            "Winner %":        ["41.3%", "38.6%", "28.7%", "26.8%"],
            "Top-3 %":         ["61.5%", "55.6%", "45.2%", "46.8%"],
            "Avg. Rank Error": ["3.4", "4.9", "7.1", "8.6"],
        })
        st.dataframe(_bt_fis, use_container_width=True, hide_index=True)
        st.caption(
            "Higher Rho on FIS vs World Cup reflects clearer field hierarchies at the FIS level — "
            "the spread between athletes is wider, making relative ranking more predictable. "
            "The model trains and predicts on the same race level when a FIS race type is selected."
        )

    st.markdown(
        """
        **How to read these numbers:**

        - **Spearman Rho** — rank correlation between predicted and actual finishing order among
          finishers (1.0 = perfect, 0 = no relationship).
        - **Winner %** — fraction of races where the model's top-ranked athlete actually won.
          A random pick from a 60-athlete field would win roughly 1.7% of the time.
        - **Top-3 %** — fraction of actual podium athletes captured in the model's predicted top 3.
        - **Avg. Rank Error** — mean positional error across all finishers. Predicting by bib
          order alone averages 9–10 positions of error.
        """
    )

# ---------------------------------------------------------------------------
# Cached wrappers around v11
# ---------------------------------------------------------------------------

@st.cache_data(ttl=604800, show_spinner=False)
def cached_train(discipline: str, sex: str, race_type: str):
    """Train v11 XGBRanker on history for the given race type. Returns (model, hist_df)."""
    return v11.train(discipline, sex, race_type)


@st.cache_data(ttl=604800, show_spinner=False)
def cached_list_venues(discipline: str, sex: str, race_type: str) -> list[str]:
    return v11.list_venues(discipline, sex, race_type)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

st.sidebar.header("Race Setup")

sel_disc      = st.sidebar.selectbox("Discipline", ["Slalom", "Giant Slalom", "Super G", "Downhill"])
sel_race_type = st.sidebar.selectbox("Race Type", [
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
])
sex_label     = st.sidebar.radio("Sex", ["Men (M)", "Women (F)"])
sex_code      = "Men's" if sex_label.startswith("Men") else "Women's"

venues = cached_list_venues(sel_disc, sex_code, sel_race_type)
if venues:
    sel_venue = st.sidebar.selectbox("Venue", venues)
else:
    sel_venue = st.sidebar.text_input("Venue", placeholder="e.g. Wengen")

race_month = st.sidebar.number_input(
    "Race Month", min_value=1, max_value=12,
    value=date.today().month,
    help="Month the race is held — used as a seasonal signal.",
)

with st.sidebar.expander("Weather (optional)"):
    temp_on  = st.checkbox("Set temperature", value=False)
    temp_val = st.slider("Air temperature (°C)", -20, 10, -5, disabled=not temp_on)
    cloud_on = st.checkbox("Set cloud cover", value=False)
    cloud_val= st.slider("Cloud cover (%)", 0, 100, 50, disabled=not cloud_on)
    precip_on= st.checkbox("Set precipitation", value=False)
    precip_val=st.number_input("Precipitation (mm, 24h)", 0.0, 50.0, 0.0, 0.5, disabled=not precip_on)

weather_dict = {}
if temp_on:
    weather_dict["air_temp_c"]    = float(temp_val)
if cloud_on:
    weather_dict["cloud_cover"]   = float(cloud_val)
if precip_on:
    weather_dict["precip_24h_mm"] = float(precip_val)

st.sidebar.markdown("---")
st.sidebar.caption(
    f"Model v11 — trained on {sel_race_type} history.  \n"
    "XGBRanker with discipline-specific hyperparameters, EWM form, "
    "venue history, DNF risk, form trajectory, and weather signals."
)

# ---------------------------------------------------------------------------
# Start list upload
# ---------------------------------------------------------------------------

st.subheader("Start List")

col_info, col_tmpl = st.columns([3, 1])
with col_info:
    st.markdown(
        "Upload a CSV with **Bib** and **FIS_Code** columns. "
        "An optional **Name** column sets athlete display names."
    )
with col_tmpl:
    template_csv = "Bib,FIS_Code,Name\n1,422304,KRISTOFFERSEN Henrik\n2,512182,MEILLARD Loic\n3,6190403,NOEL Clement\n"
    st.download_button(
        "Download template",
        data=template_csv,
        file_name="start_list_template.csv",
        mime="text/csv",
    )

uploaded = st.file_uploader("Upload start list CSV", type=["csv"])

start_list  = None
parse_error = None

if uploaded is not None:
    try:
        raw_df = pd.read_csv(uploaded)
        raw_df.columns = [c.strip().lower().replace(" ", "_") for c in raw_df.columns]

        if "bib" not in raw_df.columns:
            parse_error = "CSV must have a 'Bib' column."
        elif "fis_code" not in raw_df.columns:
            parse_error = "CSV must have a 'FIS_Code' column."
        else:
            raw_df["bib"]      = pd.to_numeric(raw_df["bib"], errors="coerce")
            raw_df["fis_code"] = pd.to_numeric(raw_df["fis_code"], errors="coerce")
            raw_df = raw_df.dropna(subset=["bib", "fis_code"])
            raw_df["bib"]      = raw_df["bib"].astype(int)
            raw_df["fis_code"] = raw_df["fis_code"].astype(str).str.strip()

            if "name" not in raw_df.columns:
                raw_df["name"] = raw_df["fis_code"]

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
        f"{sel_disc} · World Cup · {sel_venue or '(no venue)'} · {sex_label}"
    )

    with st.expander("Preview start list"):
        st.dataframe(start_list[["bib", "fis_code", "name"]], use_container_width=True, hide_index=True)

    run = st.button("Run Prediction", type="primary")

    if run:
        with st.spinner("Loading race history and training model..."):
            model, hist_df = cached_train(sel_disc, sex_code, sel_race_type)

        with st.spinner("Building athlete features and predicting..."):
            pred_df = v11.predict(
                model              = model,
                hist_df            = hist_df,
                start_list         = start_list,
                venue              = sel_venue or "",
                race_month         = int(race_month),
                weather_conditions = weather_dict or None,
                discipline         = sel_disc,
            )
            pred_df = pred_df.rename(columns={"rank": "#"})

        # ----------------------------------------------------------------
        # Summary metrics
        # ----------------------------------------------------------------
        st.markdown("---")
        st.subheader("Predicted Ranking")

        winner_row     = pred_df.iloc[0]
        top3_lastnames = " / ".join(n.split()[-1] for n in pred_df.head(3)["name"].tolist())

        # Breakout: biggest gap between bib rank and predicted rank (bib > 5 only)
        pred_df["_bib_rank"] = pred_df["bib"].rank(method="min").astype(int)
        pred_df["_improve"]  = pred_df["_bib_rank"] - pred_df["#"]
        outsiders = pred_df[pred_df["_bib_rank"] > 5]
        if not outsiders.empty and outsiders["_improve"].max() > 0:
            breakout       = outsiders.nlargest(1, "_improve").iloc[0]
            breakout_name  = breakout["name"]
            breakout_delta = f"Bib {int(breakout['bib'])} → Pred. #{int(breakout['#'])}"
        else:
            breakout       = pred_df.iloc[1]
            breakout_name  = breakout["name"]
            breakout_delta = f"Score: {breakout['pred_score']:.2f}"

        m1, m2, m3 = st.columns(3)
        m1.metric("Predicted Winner", winner_row["name"], f"Score: {winner_row['pred_score']:.2f}")
        m2.metric("Top-3 Favorites", top3_lastnames)
        m3.metric("Breakout Pick", breakout_name, breakout_delta)

        # ----------------------------------------------------------------
        # Results table
        # ----------------------------------------------------------------
        display = pred_df[["#", "bib", "name", "pred_score", "ewm_shrunk",
                            "venue_shrunk", "form_slope", "weather_adj",
                            "venue_n", "n_career"]].copy()
        display["pred_score"]  = display["pred_score"].round(3)
        display["ewm_shrunk"]  = display["ewm_shrunk"].round(3)
        display["venue_shrunk"]= display["venue_shrunk"].round(3)
        display["form_slope"]  = display["form_slope"].round(3)
        display["weather_adj"] = display["weather_adj"].round(3)
        display["venue_n"]     = display["venue_n"].astype(int)
        display["n_career"]    = display["n_career"].astype(int)

        st.dataframe(
            display.rename(columns={
                "#":            "#",
                "bib":          "Bib",
                "name":         "Athlete",
                "pred_score":   "Pred. Score",
                "ewm_shrunk":   "EWM Form",
                "venue_shrunk": "Venue Avg",
                "form_slope":   "Form Trend",
                "weather_adj":  "Weather Adj",
                "venue_n":      "Venue Starts",
                "n_career":     "Career Starts",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "#":            st.column_config.NumberColumn("#",            help="Predicted rank"),
                "Bib":          st.column_config.NumberColumn("Bib"),
                "Athlete":      st.column_config.TextColumn("Athlete"),
                "Pred. Score":  st.column_config.NumberColumn("Pred. Score", help="Ranking score — higher is better", format="%.3f"),
                "EWM Form":     st.column_config.NumberColumn("EWM Form",    help="Shrinkage-adjusted exponentially weighted mean z-score. Positive = above average", format="%.3f"),
                "Venue Avg":    st.column_config.NumberColumn("Venue Avg",   help="Shrinkage-adjusted mean z-score at this venue", format="%.3f"),
                "Form Trend":   st.column_config.NumberColumn("Form Trend",  help="Recent form trajectory (positive = improving). Applied for SL and GS only.", format="%.3f"),
                "Weather Adj":  st.column_config.NumberColumn("Weather Adj", help="Weather performance adjustment (0 if no weather set or no athlete weather history)", format="%.3f"),
                "Venue Starts": st.column_config.NumberColumn("Venue Starts",help="Prior World Cup starts at this venue"),
                "Career Starts":st.column_config.NumberColumn("Career Starts",help="Total prior WC starts in this discipline"),
            },
        )

        # ----------------------------------------------------------------
        # Predicted score bar chart — top 15
        # ----------------------------------------------------------------
        st.markdown("#### Predicted Score — Top 15")

        chart_df = pred_df.head(15).sort_values("pred_score", ascending=True)
        colors   = ["#1a3a6b" if i == len(chart_df) - 1 else "steelblue"
                    for i in range(len(chart_df))]

        fig = go.Figure(go.Bar(
            y           = chart_df["name"],
            x           = chart_df["pred_score"],
            orientation = "h",
            marker_color= colors,
            opacity     = 0.85,
            hovertemplate = "<b>%{y}</b><br>Pred. Score: %{x:.3f}<extra></extra>",
        ))
        fig.update_layout(
            xaxis  = dict(title="Predicted ranking score (higher = faster)"),
            yaxis  = dict(title="", automargin=True),
            height = max(320, 28 * len(chart_df)),
            margin = dict(l=170, r=40, t=20, b=50),
            plot_bgcolor  = "white",
            paper_bgcolor = "white",
        )
        fig.update_xaxes(showgrid=True, gridcolor="#eee")
        fig.update_yaxes(showgrid=False)
        st.plotly_chart(fig, use_container_width=True)

        # ----------------------------------------------------------------
        # EWM form vs venue chart
        # ----------------------------------------------------------------
        st.markdown("#### EWM Form vs Venue History — Top 20")
        scatter_df = pred_df.head(20)

        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(
            x    = scatter_df["ewm_shrunk"],
            y    = scatter_df["venue_shrunk"],
            mode = "markers+text",
            text = scatter_df["name"].str.split().str[-1],
            textposition = "top center",
            textfont     = dict(size=10),
            marker       = dict(
                size  = 10,
                color = scatter_df["pred_score"],
                colorscale = "Blues",
                showscale  = True,
                colorbar   = dict(title="Pred. Score"),
            ),
            hovertemplate = (
                "<b>%{text}</b><br>"
                "EWM Form: %{x:.3f}<br>"
                "Venue Avg: %{y:.3f}<extra></extra>"
            ),
        ))
        fig2.add_hline(y=0, line_dash="dot", line_color="gray", opacity=0.4)
        fig2.add_vline(x=0, line_dash="dot", line_color="gray", opacity=0.4)
        fig2.update_layout(
            xaxis  = dict(title="EWM Form (z-score, shrunk)"),
            yaxis  = dict(title="Venue Average (z-score, shrunk)"),
            height = 420,
            margin = dict(l=60, r=40, t=20, b=60),
            plot_bgcolor  = "white",
            paper_bgcolor = "white",
        )
        fig2.update_xaxes(showgrid=True, gridcolor="#eee")
        fig2.update_yaxes(showgrid=True, gridcolor="#eee")
        st.plotly_chart(fig2, use_container_width=True)

        # ----------------------------------------------------------------
        # Export
        # ----------------------------------------------------------------
        export_cols = ["#", "bib", "name", "pred_score", "ewm_shrunk",
                       "venue_shrunk", "venue_n", "form_slope", "weather_adj", "n_career"]
        csv_out = pred_df[[c for c in export_cols if c in pred_df.columns]].to_csv(index=False)
        st.download_button(
            "Download predictions CSV",
            data     = csv_out,
            file_name= f"xgb_v11_{sel_disc.lower().replace(' ','_')}_{sel_venue or 'venue'}.csv",
            mime     = "text/csv",
        )

else:
    st.info(
        "Select a discipline, sex, and venue in the sidebar, "
        "then upload a start list CSV to run the prediction."
    )
