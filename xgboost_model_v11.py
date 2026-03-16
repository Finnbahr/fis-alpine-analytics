"""
xgboost_model_v11.py — Alpine Analytics XGBoost v11 race predictor.

v5 backbone + v6 momentum/variance-cross-terms + v7 weather + v8 bounce/re-dnf
+ v11 tuning: dynamic bounce (no leakage), discipline-specific fills + hyperparams.

Public API
----------
load_history(discipline, sex, race_type)          -> pd.DataFrame
train(discipline, sex, race_type)                 -> (XGBRanker, pd.DataFrame)
predict(model, hist_df, start_list, venue,
        race_month, weather_conditions=None,
        discipline=None, today=None)              -> pd.DataFrame
list_venues(discipline, sex, race_type)           -> list[str]

Backtested accuracy (World Cup, 2021+ walk-forward, finishers-only evaluation,
dynamic bounce + discipline-specific fills + per-disc hyperparams):
    Discipline   | Races (M/W) | Rho (M/W)       | Winner % (M/W)
    -------------|-------------|-----------------|----------------
    Slalom       | 58 / 53     | 0.562 / 0.634   | 24.1% / 52.8%
    Giant Slalom | 48 / 50     | 0.646 / 0.690   | 56.2% / 28.0%
    Super G      | 39 / 46     | 0.683 / 0.755   | 41.0% / 30.4%
    Downhill     | 47 / 44     | 0.692 / 0.763   | 29.8% / 36.4%

    Combined M+W winner%: SL=38.5%  GS=42.1%  SG=35.7%  DH=33.1%
    Combined M+W rho:     SL=0.598  GS=0.668  SG=0.719  DH=0.728
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from xgboost import XGBRanker
from sqlalchemy import text
from database import get_engine

# ── Per-discipline constants ───────────────────────────────────────────────────
# EWM halflives in RACE COUNTS (not days)
EWM_HL = {
    "Slalom": 2, "Giant Slalom": 3, "Super G": 8, "Downhill": 9,
}
EWM_HL_SHORT = 2    # fixed short halflife (race counts)
EWM_HL_LONG  = 12   # fixed long halflife (race counts)
MEAN_K       = 3    # Bayesian shrinkage toward 0 for thin history

VENUE_SHRINK = {
    "Slalom": 5, "Giant Slalom": 10, "Super G": 10, "Downhill": 10,
}

# WEATHER_K — re-calibrated on 2026 data (SL=500, GS=200, SG=500, DH=100)
WEATHER_K = {
    "Slalom": 500, "Giant Slalom": 200, "Super G": 500, "Downhill": 100,
}
WEATHER_CAP_PER = 0.20
WEATHER_CAP_TOT = 0.30

# Discipline-specific NaN fill defaults (tuned to each discipline's race cadence and DNF rates)
_DAYS_SINCE_FILL = {"Slalom": 7,    "Giant Slalom": 14,  "Super G": 28,  "Downhill": 28}
_DNF_RATE_FILL   = {"Slalom": 0.12, "Giant Slalom": 0.08, "Super G": 0.06, "Downhill": 0.05}
_STD_FILL        = {"Slalom": 0.45, "Giant Slalom": 0.55, "Super G": 0.65, "Downhill": 0.70}

# Per-discipline XGBoost hyperparameters (tuned via walk-forward backtest 2021+)
# SL: extra L2 (lam2) reduces over-reliance on win-history features in deep field
# DH: high gamma (1.0) needed for specialist-concentrated winner pattern
_XGB_BASE = dict(
    objective="rank:pairwise", n_estimators=600, learning_rate=0.04,
    subsample=0.8, colsample_bytree=0.75, min_child_weight=3,
    random_state=42, n_jobs=-1, verbosity=0,
)
XGB_PARAMS_BY_DISC = {
    "Slalom":       {**_XGB_BASE, "max_depth": 5, "gamma": 0.3, "reg_alpha": 0.1, "reg_lambda": 2},
    "Giant Slalom": {**_XGB_BASE, "max_depth": 5, "gamma": 0.3, "reg_alpha": 0.1},
    "Super G":      {**_XGB_BASE, "max_depth": 5, "gamma": 0.3, "reg_alpha": 0.1},
    "Downhill":     {**_XGB_BASE, "max_depth": 4, "gamma": 1.0},
}
# Fallback for unknown disciplines
XGB_PARAMS = {**_XGB_BASE, "max_depth": 5, "gamma": 0.3, "reg_alpha": 0.1}

ALL_FEATURES = [
    # Core EWM form
    "ewm_mean_z", "ewm_shrunk", "ewm_short_z", "ewm_long_z",
    # Rolling
    "roll3_mean_z", "roll5_mean_z", "roll10_mean_z", "roll5_std_z",
    # Last race
    "last_race_z", "last_race_podium", "last_race_win", "last_race_dnf",
    # DNF / consistency
    "roll5_dnf_rate", "career_dnf_rate", "career_std_z", "cv_ratio",
    # Career / wins
    "n_career", "n_wins_career", "n_wins_5", "n_wins_10",
    "win_rate_career", "win_rate_5",
    "podium_rate_5", "podium_rate_10", "podium_rate_career",
    "win_conv_career", "win_conv_5",
    "consec_podiums", "consec_wins", "top10_rate_10", "season_best_z",
    # Slope / momentum (v6)
    "form_slope",
    "momentum_z_norm",   # (last_race_z - ewm_mean_z) / (career_std_z + 0.1)
    "rel_momentum",      # momentum_z_norm minus field mean
    # Variance x form cross-terms (v6)
    "std_x_shrunk",      # career_std_z * ewm_shrunk
    "std_x_slope",       # career_std_z * form_slope
    # Bounce-back (v5/v7/v8)
    "bounce_back_z",
    "bounce_interact",       # bounce_back_z * last_race_dnf * max(0, ewm_shrunk)
    "re_dnf_rate",           # prob of consecutive DNF (v8)
    "last_dnf_x_bounce_z",   # last_race_dnf * bounce_back_z (v8)
    "re_dnf_interact",       # last_race_dnf * re_dnf_rate (v8)
    # Venue
    "venue_mean_z_raw", "venue_shrunk", "venue_n", "venue_win_rate", "venue_best_z",
    # FIS + context
    "roll5_mean_fis", "roll10_mean_fis", "bib", "days_since", "month",
    # Weather (v7)
    "weather_adj", "has_weather",
    # Field-relative
    "rel_ewm_z", "rel_venue_z", "rel_fis", "rel_slope", "rel_win_conv",
    "field_mean_z", "field_std_z",
]


# ── EWM helpers ────────────────────────────────────────────────────────────────

def _alpha(hl: float) -> float:
    """Convert race-count halflife to EWM alpha."""
    return 1.0 - np.exp(-np.log(2) / hl)


# ── WLS form slope ─────────────────────────────────────────────────────────────

def _wls_slope(fis_grp: pd.DataFrame, lam: float) -> pd.Series:
    """
    Weighted least-squares slope of z_score ~ days_ago for each race row.
    Weights decay exponentially by days_ago. Returns -slope so that positive
    value = athlete improving (recent races better than older).
    Uses reset_index internally; caller must assign with .values for positional alignment.
    """
    fis_grp = fis_grp.sort_values("date").reset_index(drop=True)
    dates   = fis_grp["date"].values
    zscores = fis_grp["race_z_score"].values
    slopes  = np.full(len(fis_grp), np.nan)

    for i in range(len(fis_grp)):
        if i < 2:
            continue
        past_z = zscores[:i].astype(float)
        past_d = ((dates[i] - dates[:i]) / np.timedelta64(1, "D")).astype(float)
        valid  = ~np.isnan(past_z)
        if valid.sum() < 2:
            continue
        w   = np.exp(-lam * past_d[valid])
        z   = past_z[valid]
        d   = past_d[valid]
        W   = w.sum()
        Wx  = (w * d).sum()
        Wy  = (w * z).sum()
        Wxx = (w * d * d).sum()
        Wxy = (w * d * z).sum()
        den = W * Wxx - Wx * Wx
        if abs(den) < 1e-10:
            continue
        slopes[i] = -(W * Wxy - Wx * Wy) / den   # negated: positive = improving

    return pd.Series(slopes, index=fis_grp.index)


# ── Dynamic bounce helpers ─────────────────────────────────────────────────────

def _compute_dynamic_bounce(fis_grp: pd.DataFrame) -> pd.DataFrame:
    """
    Expanding-window bounce_back_z and re_dnf_rate — no leakage from future races.
    bounce_back_z[i] = mean z-score of all prior races that immediately followed a DNF
    re_dnf_rate[i]   = P(DNF | prior DNF) from all pairs before race i
    Both values at race i reflect only races strictly before race i.
    """
    fis_grp = fis_grp.sort_values("date").reset_index(drop=True)
    n      = len(fis_grp)
    is_dnf = fis_grp["is_dnf"].values.astype(float)
    z      = fis_grp["race_z_score"].values.astype(float)

    bb_out   = np.full(n, np.nan)
    rdnf_out = np.full(n, np.nan)
    bb_sum = 0.0; bb_n = 0
    re_n = 0;    re_hit = 0

    for i in range(n):
        if bb_n   > 0: bb_out[i]   = bb_sum / bb_n
        if re_n   > 0: rdnf_out[i] = re_hit / re_n
        if i >= 1 and is_dnf[i - 1] == 1.0:
            re_n += 1
            if is_dnf[i] == 1.0: re_hit += 1
            if not np.isnan(z[i]): bb_sum += z[i]; bb_n += 1

    return pd.DataFrame(
        {"bounce_back_z": bb_out, "re_dnf_rate": rdnf_out},
        index=fis_grp.index,
    )


def _bounce_stats_for_athlete(ath_df: pd.DataFrame) -> tuple[float, float]:
    """
    Compute bounce_back_z and re_dnf_rate from an athlete's full history.
    Used in predict() — all history is available (no leakage in prediction context).
    """
    ath_df = ath_df.sort_values("date").reset_index(drop=True)
    is_dnf = ath_df["is_dnf"].values.astype(float)
    z      = ath_df["race_z_score"].fillna(float("nan")).values.astype(float)
    n = len(ath_df)
    bb_sum = 0.0; bb_n = 0; re_n = 0; re_hit = 0
    for i in range(1, n):
        if is_dnf[i - 1] == 1.0:
            re_n += 1
            if is_dnf[i] == 1.0: re_hit += 1
            if not np.isnan(z[i]): bb_sum += z[i]; bb_n += 1
    bb_z = bb_sum / bb_n if bb_n > 0 else 0.0
    rdnf = re_hit / re_n if re_n > 0 else 0.0
    return float(bb_z), float(rdnf)


# ── Weather helpers ────────────────────────────────────────────────────────────

def _load_race_weather(engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text("""
            SELECT race_id::int AS race_id,
                   air_temp_c::float, cloud_cover::float, precip_24h_mm::float
            FROM raw.race_weather
        """), conn)


def _load_athlete_weather(engine, discipline: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text("""
            SELECT fis_code::text AS fis_code,
                   condition, condition_bin, avg_z_score::float, race_count::int
            FROM athlete_aggregate.weather_performance
            WHERE discipline = :disc AND race_type = 'World Cup'
        """), conn, params={"disc": discipline})


def _compute_weather_adj(
    df: pd.DataFrame,
    discipline: str,
    race_weather: pd.DataFrame,
    ath_weather: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute per-athlete weather adjustment for each race using categorical condition bins.
    Mirrors v7 MC weather logic: bin race conditions, Bayesian shrinkage, per-condition cap.
    """
    wk = WEATHER_K[discipline]

    # Merge race conditions
    df = df.merge(race_weather, on="race_id", how="left")
    df["has_weather"] = (~df["air_temp_c"].isna()).astype(float)

    # Bin race conditions into categorical labels (must match weather_performance.condition_bin)
    df["_temp_bin"] = pd.cut(
        df["air_temp_c"],
        bins=[-999, -5, 2, 999],
        labels=["Cold (<-5°C)", "Cool (-5–2°C)", "Warm (>2°C)"],
    ).astype(object)
    df["_cloud_bin"] = pd.cut(
        df["cloud_cover"].astype("float64"),
        bins=[-1, 29.9, 70, 101],
        labels=["Clear (<30%)", "Partly Cloudy (30–70%)", "Overcast (>70%)"],
    ).astype(object)
    df["_precip_bin"] = pd.cut(
        df["precip_24h_mm"],
        bins=[-0.001, 0.499, 5.0, 9999],
        labels=["Dry (<0.5mm)", "Light (0.5–5mm)", "Heavy (>5mm)"],
    ).astype(object)

    total_adj = pd.Series(0.0, index=df.index)
    for cond, bin_col in [
        ("temperature",   "_temp_bin"),
        ("cloud_cover",   "_cloud_bin"),
        ("precipitation", "_precip_bin"),
    ]:
        cond_lkp = (
            ath_weather[ath_weather["condition"] == cond][
                ["fis_code", "condition_bin", "avg_z_score", "race_count"]
            ]
            .groupby(["fis_code", "condition_bin"], as_index=False)
            .agg(avg_z_score=("avg_z_score", "mean"),
                 race_count=("race_count", "sum"))
        )
        merged = df[["fis_code", bin_col]].merge(
            cond_lkp,
            left_on=["fis_code", bin_col],
            right_on=["fis_code", "condition_bin"],
            how="left",
        )
        n   = merged["race_count"].fillna(0).values
        z   = merged["avg_z_score"].fillna(0).values
        adj = np.clip(n * z / (n + wk), -WEATHER_CAP_PER, WEATHER_CAP_PER)
        adj[np.isnan(adj)] = 0.0
        total_adj = total_adj + adj

    df["weather_adj"] = np.clip(total_adj.values, -WEATHER_CAP_TOT, WEATHER_CAP_TOT)
    df = df.drop(
        columns=["air_temp_c", "cloud_cover", "precip_24h_mm",
                 "_temp_bin", "_cloud_bin", "_precip_bin"],
        errors="ignore",
    )
    return df


# ── Feature engineering ────────────────────────────────────────────────────────

def _build_features(
    df: pd.DataFrame,
    discipline: str,
    race_weather: pd.DataFrame,
    ath_weather: pd.DataFrame,
) -> pd.DataFrame:
    """
    Full v11 feature engineering. All rolling/EWM features use shift(1) to
    prevent leakage from the current race into training features.
    Bounce features computed via expanding window — no static table dependency.
    """
    hl   = EWM_HL[discipline]
    v_k  = VENUE_SHRINK[discipline]
    lam  = np.log(2) / (hl * 30)           # day-based decay for WLS slope

    a     = _alpha(hl)
    a_s   = _alpha(EWM_HL_SHORT)
    a_l   = _alpha(EWM_HL_LONG)

    std_fill = _STD_FILL[discipline]
    dnf_fill = _DNF_RATE_FILL[discipline]
    ds_fill  = _DAYS_SINCE_FILL[discipline]

    df = df.sort_values(["fis_code", "date"]).reset_index(drop=True)
    g  = df.groupby("fis_code")

    # ── EWM z-score (lagged) ──
    df["ewm_mean_z"]  = g["race_z_score"].transform(
        lambda x: x.shift(1).ewm(alpha=a,   adjust=True, ignore_na=True).mean())
    df["ewm_short_z"] = g["race_z_score"].transform(
        lambda x: x.shift(1).ewm(alpha=a_s, adjust=True, ignore_na=True).mean())
    df["ewm_long_z"]  = g["race_z_score"].transform(
        lambda x: x.shift(1).ewm(alpha=a_l, adjust=True, ignore_na=True).mean())

    # ── Rolling z ──
    df["roll3_mean_z"]  = g["race_z_score"].transform(lambda x: x.shift(1).rolling(3,  min_periods=1).mean())
    df["roll5_mean_z"]  = g["race_z_score"].transform(lambda x: x.shift(1).rolling(5,  min_periods=1).mean())
    df["roll10_mean_z"] = g["race_z_score"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())
    df["roll5_std_z"]   = g["race_z_score"].transform(lambda x: x.shift(1).rolling(5,  min_periods=2).std())

    # ── Last-race signals ──
    df["last_race_z"]      = g["race_z_score"].transform(lambda x: x.shift(1))
    df["last_race_dnf"]    = g["is_dnf"].transform(lambda x: x.shift(1).astype(float))
    df["last_race_podium"] = g["actual_rank"].transform(lambda x: (x.shift(1) <= 3).astype(float))
    df["last_race_win"]    = g["actual_rank"].transform(lambda x: (x.shift(1) == 1).astype(float))

    # ── DNF rates ──
    df["roll5_dnf_rate"]  = g["is_dnf"].transform(lambda x: x.shift(1).rolling(5, min_periods=1).mean())
    df["career_dnf_rate"] = g["is_dnf"].transform(lambda x: x.shift(1).expanding().mean())

    # ── Career counts ──
    df["n_career"]     = g["race_z_score"].transform(lambda x: x.shift(1).expanding().count())
    df["career_std_z"] = g["race_z_score"].transform(lambda x: x.shift(1).expanding().std())

    is_win    = (df["actual_rank"] == 1).astype(float)
    is_podium = (df["actual_rank"] <= 3).astype(float)
    is_top10  = (df["actual_rank"] <= 10).astype(float)
    df["_is_win"]    = is_win
    df["_is_podium"] = is_podium
    df["_is_top10"]  = is_top10

    df["n_wins_career"]      = g["_is_win"].transform(lambda x: x.shift(1).expanding().sum())
    df["n_wins_5"]           = g["_is_win"].transform(lambda x: x.shift(1).rolling(5,  min_periods=1).sum())
    df["n_wins_10"]          = g["_is_win"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).sum())
    df["win_rate_career"]    = g["_is_win"].transform(lambda x: x.shift(1).expanding().mean())
    df["win_rate_5"]         = g["_is_win"].transform(lambda x: x.shift(1).rolling(5,  min_periods=1).mean())
    df["podium_rate_5"]      = g["_is_podium"].transform(lambda x: x.shift(1).rolling(5,  min_periods=1).mean())
    df["podium_rate_10"]     = g["_is_podium"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())
    df["podium_rate_career"] = g["_is_podium"].transform(lambda x: x.shift(1).expanding().mean())
    df["top10_rate_10"]      = g["_is_top10"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())

    eps = 1e-6
    df["win_conv_career"] = df["win_rate_career"] / (df["podium_rate_career"] + eps)
    df["win_conv_5"]      = df["win_rate_5"]      / (df["podium_rate_5"]      + eps)

    # ── Consecutive streaks ──
    def _consec(series, thresh_fn):
        vals = series.shift(1).values
        out, streak = np.zeros(len(vals)), 0
        for i, v in enumerate(vals):
            if pd.isna(v):
                streak = 0
            elif thresh_fn(v):
                streak += 1
            else:
                streak = 0
            out[i] = streak
        return out

    df["consec_podiums"] = g["actual_rank"].transform(
        lambda x: pd.Series(_consec(x, lambda v: v <= 3), index=x.index))
    df["consec_wins"] = g["actual_rank"].transform(
        lambda x: pd.Series(_consec(x, lambda v: v == 1), index=x.index))

    # ── Season best z (ski-season: Sep–Aug) ──
    df["_season"] = df["date"].dt.year.where(df["date"].dt.month >= 9, df["date"].dt.year - 1)
    df = df.sort_values(["fis_code", "_season", "date"]).reset_index(drop=True)
    g2 = df.groupby(["fis_code", "_season"])
    df["season_best_z"] = g2["race_z_score"].transform(lambda x: x.shift(1).expanding().max())

    # ── WLS form slope — use .values for positional assignment ──
    df = df.sort_values(["fis_code", "date"]).reset_index(drop=True)
    df["form_slope"] = df.groupby("fis_code", group_keys=False).apply(
        lambda g: _wls_slope(g, lam), include_groups=False
    ).values

    # ── FIS rolling ──
    gf = df.groupby("fis_code")
    df["roll5_mean_fis"]  = gf["fis_points"].transform(lambda x: x.shift(1).rolling(5,  min_periods=1).mean())
    df["roll10_mean_fis"] = gf["fis_points"].transform(lambda x: x.shift(1).rolling(10, min_periods=1).mean())

    # ── Days since / month / bib ──
    df["days_since"] = gf["date"].transform(lambda x: x.diff().dt.days)
    df = df.sort_values(["date", "race_id", "fis_code"]).reset_index(drop=True)
    df["month"] = df["date"].dt.month
    df["bib"] = df.groupby("race_id")["bib"].transform(
        lambda x: x.fillna(x.median()).fillna(30)
    )

    # ── Imputation (before ewm_shrunk — needs ewm_mean_z) ──
    df["ewm_mean_z"]   = df["ewm_mean_z"].fillna(0.0)
    df["ewm_short_z"]  = df["ewm_short_z"].fillna(df["ewm_mean_z"])
    df["ewm_long_z"]   = df["ewm_long_z"].fillna(df["ewm_mean_z"])
    df["roll3_mean_z"] = df["roll3_mean_z"].fillna(df["ewm_mean_z"])
    df["roll5_mean_z"] = df["roll5_mean_z"].fillna(df["ewm_mean_z"])
    df["roll10_mean_z"]= df["roll10_mean_z"].fillna(df["ewm_mean_z"])
    df["last_race_z"]  = df["last_race_z"].fillna(0.0)
    df["career_std_z"] = df["career_std_z"].fillna(std_fill)

    # ── ewm_shrunk: Bayesian shrinkage of ewm_mean_z toward 0 (MEAN_K=3) ──
    df["ewm_shrunk"] = (df["n_career"] * df["ewm_mean_z"]) / (df["n_career"] + MEAN_K)

    # ── CV ratio ──
    df["cv_ratio"] = df["career_std_z"] / (df["ewm_shrunk"].abs() + 0.1)

    # ── Momentum (normalised surprise vs ewm_mean_z) ──
    df["momentum_z_norm"] = (
        (df["last_race_z"] - df["ewm_mean_z"]) / (df["career_std_z"] + 0.1)
    ).clip(-3, 3).fillna(0.0)

    # ── Variance × form cross-terms (v6) ──
    df["std_x_shrunk"] = (df["career_std_z"] * df["ewm_shrunk"]).fillna(0.0)
    df["std_x_slope"]  = (df["career_std_z"] * df["form_slope"]).fillna(0.0)

    # ── Venue features ──
    df = df.sort_values(["fis_code", "location", "date"]).reset_index(drop=True)
    gv = df.groupby(["fis_code", "location"])
    df["venue_mean_z_raw"] = gv["race_z_score"].transform(lambda x: x.shift(1).expanding().mean())
    df["venue_n"]          = gv["race_z_score"].transform(lambda x: x.shift(1).expanding().count())
    df["venue_win_rate"]   = gv["_is_win"].transform(lambda x: x.shift(1).expanding().mean())
    df["venue_best_z"]     = gv["race_z_score"].transform(lambda x: x.shift(1).expanding().max())
    df = df.sort_values(["fis_code", "date"]).reset_index(drop=True)

    df["venue_mean_z_raw"] = df["venue_mean_z_raw"].fillna(df["ewm_shrunk"])
    df["venue_n"]          = df["venue_n"].fillna(0)
    df["venue_win_rate"]   = df["venue_win_rate"].fillna(0.0)
    df["venue_best_z"]     = df["venue_best_z"].fillna(df["ewm_shrunk"])

    # Venue shrinkage toward ewm_shrunk (not toward 0)
    df["venue_shrunk"] = (
        df["venue_n"] * df["venue_mean_z_raw"] + v_k * df["ewm_shrunk"]
    ) / (df["venue_n"] + v_k)

    # ── Bounce-back + re-DNF — dynamic expanding window (no static table) ──
    df = df.sort_values(["fis_code", "date"]).reset_index(drop=True)
    bounce_result = df.groupby("fis_code", group_keys=False).apply(
        _compute_dynamic_bounce, include_groups=False
    )
    df["bounce_back_z"] = bounce_result["bounce_back_z"].values
    df["re_dnf_rate"]   = bounce_result["re_dnf_rate"].values
    df["bounce_back_z"] = df["bounce_back_z"].fillna(0.0)
    df["re_dnf_rate"]   = df["re_dnf_rate"].fillna(0.0)

    df["bounce_interact"]     = df["bounce_back_z"] * df["last_race_dnf"] * df["ewm_shrunk"].clip(lower=0)
    df["last_dnf_x_bounce_z"] = df["last_race_dnf"] * df["bounce_back_z"]
    df["re_dnf_interact"]     = df["last_race_dnf"] * df["re_dnf_rate"]

    # ── Weather (v7) ──
    df = df.sort_values(["date", "race_id", "fis_code"]).reset_index(drop=True)
    df = _compute_weather_adj(df, discipline, race_weather, ath_weather)
    df["weather_adj"] = df["weather_adj"].fillna(0.0)
    df["has_weather"] = df["has_weather"].fillna(0.0)

    # ── Within-race relative features ──
    race_agg = df.groupby("race_id").agg(
        field_mean_z        = ("ewm_shrunk",      "mean"),
        field_std_z         = ("ewm_shrunk",      "std"),
        field_mean_venue    = ("venue_shrunk",     "mean"),
        field_mean_fis      = ("roll5_mean_fis",   "mean"),
        field_mean_slope    = ("form_slope",       "mean"),
        field_mean_wconv    = ("win_conv_career",  "mean"),
        field_mean_momentum = ("momentum_z_norm",  "mean"),
    ).reset_index()

    df = df.merge(race_agg, on="race_id", how="left")
    df["rel_ewm_z"]    = df["ewm_shrunk"]       - df["field_mean_z"]
    df["rel_venue_z"]  = df["venue_shrunk"]     - df["field_mean_venue"]
    df["rel_fis"]      = df["roll5_mean_fis"]   - df["field_mean_fis"]
    df["rel_slope"]    = df["form_slope"]       - df["field_mean_slope"]
    df["rel_win_conv"] = df["win_conv_career"]  - df["field_mean_wconv"]
    df["rel_momentum"] = df["momentum_z_norm"]  - df["field_mean_momentum"]
    df["field_std_z"]  = df["field_std_z"].fillna(0.0)

    # ── Remaining imputation ──
    fis_med = float(df["fis_points"].median())
    df["roll5_mean_fis"]     = df["roll5_mean_fis"].fillna(fis_med)
    df["roll10_mean_fis"]    = df["roll10_mean_fis"].fillna(df["roll5_mean_fis"])
    df["roll5_std_z"]        = df["roll5_std_z"].fillna(std_fill)
    df["last_race_dnf"]      = df["last_race_dnf"].fillna(0.0)
    df["last_race_podium"]   = df["last_race_podium"].fillna(0.0)
    df["last_race_win"]      = df["last_race_win"].fillna(0.0)
    df["roll5_dnf_rate"]     = df["roll5_dnf_rate"].fillna(dnf_fill)
    df["career_dnf_rate"]    = df["career_dnf_rate"].fillna(dnf_fill)
    df["n_career"]           = df["n_career"].fillna(0)
    df["n_wins_career"]      = df["n_wins_career"].fillna(0)
    df["n_wins_5"]           = df["n_wins_5"].fillna(0)
    df["n_wins_10"]          = df["n_wins_10"].fillna(0)
    df["win_rate_career"]    = df["win_rate_career"].fillna(0.0)
    df["win_rate_5"]         = df["win_rate_5"].fillna(0.0)
    df["podium_rate_5"]      = df["podium_rate_5"].fillna(0.0)
    df["podium_rate_10"]     = df["podium_rate_10"].fillna(0.0)
    df["podium_rate_career"] = df["podium_rate_career"].fillna(0.0)
    df["top10_rate_10"]      = df["top10_rate_10"].fillna(0.0)
    df["win_conv_career"]    = df["win_conv_career"].fillna(0.0)
    df["win_conv_5"]         = df["win_conv_5"].fillna(0.0)
    df["consec_podiums"]     = df["consec_podiums"].fillna(0.0)
    df["consec_wins"]        = df["consec_wins"].fillna(0.0)
    df["season_best_z"]      = df["season_best_z"].fillna(0.0)
    df["form_slope"]         = df["form_slope"].fillna(0.0)
    df["last_dnf_x_bounce_z"]= df["last_dnf_x_bounce_z"].fillna(0.0)
    df["re_dnf_interact"]    = df["re_dnf_interact"].fillna(0.0)
    df["days_since"]         = df["days_since"].fillna(ds_fill)
    df["rel_ewm_z"]          = df["rel_ewm_z"].fillna(0.0)
    df["rel_venue_z"]        = df["rel_venue_z"].fillna(0.0)
    df["rel_fis"]            = df["rel_fis"].fillna(0.0)
    df["rel_slope"]          = df["rel_slope"].fillna(0.0)
    df["rel_win_conv"]       = df["rel_win_conv"].fillna(0.0)
    df["rel_momentum"]       = df["rel_momentum"].fillna(0.0)
    df["field_mean_z"]       = df["field_mean_z"].fillna(0.0)

    return df


# ── Public API ─────────────────────────────────────────────────────────────────

def load_history(
    discipline: str,
    sex: str,
    race_type: str = "World Cup",
) -> pd.DataFrame:
    """
    Load all race results and compute training features (no leakage).
    Returns DataFrame with ALL_FEATURES columns + race_z_score + race_id.
    """
    engine = get_engine()
    q = text("""
        SELECT
            fr.fis_code::text         AS fis_code,
            fr.race_id,
            rz.race_z_score::float    AS race_z_score,
            rd.date,
            rd.location,
            fr.bib::int               AS bib,
            fr.fis_points::float      AS fis_points,
            fr.rank                   AS rank_str
        FROM raw.fis_results fr
        JOIN raw.race_details rd ON rd.race_id = fr.race_id
        LEFT JOIN race_aggregate.race_z_score rz
               ON rz.race_id = fr.race_id
              AND rz.fis_code::text = fr.fis_code::text
        WHERE rd.discipline = :disc
          AND rd.race_type  = :rtype
          AND rd.sex        = :sex
        ORDER BY rd.date ASC, fr.race_id, fr.fis_code
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"disc": discipline, "rtype": race_type, "sex": sex})

    df["date"]        = pd.to_datetime(df["date"])
    df["is_dnf"]      = df["rank_str"].astype(str).str.upper().str.startswith(("DNF","DSQ","DNS"))
    df["actual_rank"] = pd.to_numeric(df["rank_str"], errors="coerce")
    df["fis_points"]  = (
        df["fis_points"]
        .fillna(df.groupby("race_id")["fis_points"].transform("median"))
        .fillna(35.0)
    )

    race_weather = _load_race_weather(engine)
    ath_weather  = _load_athlete_weather(engine, discipline)

    return _build_features(df, discipline, race_weather, ath_weather)


def train(
    discipline: str,
    sex: str,
    race_type: str = "World Cup",
) -> tuple:
    """
    Train XGBoost v11 on all available history.

    Returns
    -------
    model   : fitted XGBRanker
    hist_df : full history DataFrame (pass to predict())
    """
    hist_df    = load_history(discipline, sex, race_type)
    train_rows = hist_df[hist_df["race_z_score"].notna()].sort_values("race_id").copy()

    if len(train_rows) < 50:
        raise ValueError(
            f"Insufficient training data for {discipline}/{sex}/{race_type} "
            f"({len(train_rows)} rows, need ≥ 50)."
        )

    groups = train_rows.groupby("race_id", sort=False).size().values
    xgb_params = XGB_PARAMS_BY_DISC.get(discipline, XGB_PARAMS)
    model  = XGBRanker(**xgb_params)
    model.fit(
        train_rows[ALL_FEATURES].values.astype(float),
        train_rows["race_z_score"].values.astype(float),
        group=groups,
    )
    return model, hist_df


def predict(
    model,
    hist_df: pd.DataFrame,
    start_list: pd.DataFrame,
    venue: str,
    race_month: int,
    weather_conditions: dict = None,
    discipline: str = "Giant Slalom",
    today: pd.Timestamp = None,
) -> pd.DataFrame:
    """
    Generate ranked prediction for every athlete in start_list.

    Parameters
    ----------
    model              : fitted XGBRanker from train()
    hist_df            : history DataFrame from train()
    start_list         : DataFrame with bib, fis_code, name columns
    venue              : location string matching raw.race_details.location
    race_month         : calendar month (1–12)
    weather_conditions : dict {condition_col: value} or None for neutral weather
    discipline         : discipline string
    today              : reference date (defaults to today)

    Returns
    -------
    DataFrame sorted rank 1 = predicted winner, columns:
        rank, bib, fis_code, name, pred_score,
        ewm_shrunk, venue_shrunk, venue_n, form_slope, weather_adj, n_career
    """
    if today is None:
        today = pd.Timestamp.today().normalize()

    hl    = EWM_HL[discipline]
    v_k   = VENUE_SHRINK[discipline]
    lam   = np.log(2) / (hl * 30)
    a     = _alpha(hl)
    a_s   = _alpha(EWM_HL_SHORT)
    a_l   = _alpha(EWM_HL_LONG)
    wk    = WEATHER_K[discipline]
    wc    = weather_conditions or {}

    # ── Bounce-back: compute from hist_df (no static table, no leakage) ──
    engine = get_engine()
    bb_map   = {}
    rdnf_map = {}
    for fc, grp in hist_df.groupby("fis_code"):
        bb_z, rdnf = _bounce_stats_for_athlete(grp)
        bb_map[fc]   = bb_z
        rdnf_map[fc] = rdnf

    # Build weather adjustment map for categorical bins
    wp_map = {}   # {fis_code: {condition_bin: (avg_z, count)}}
    if wc:
        try:
            aw = _load_athlete_weather(engine, discipline)
            for _, row in aw.iterrows():
                wp_map.setdefault(row["fis_code"], {})[row["condition_bin"]] = (
                    float(row["avg_z_score"]), int(row["race_count"])
                )
        except Exception:
            pass

    def _weather_adj_for_athlete(fc: str) -> tuple[float, float]:
        """Compute (weather_adj, has_weather) for a single athlete given race conditions."""
        if not wc:
            return 0.0, 0.0
        ath_wp = wp_map.get(fc, {})
        # Bin the incoming weather_conditions dict
        bins = {}
        if "air_temp_c" in wc and wc["air_temp_c"] is not None:
            t = float(wc["air_temp_c"])
            if t <= -5:   bins["temperature"] = "Cold (<-5°C)"
            elif t <= 2:  bins["temperature"] = "Cool (-5–2°C)"
            else:         bins["temperature"] = "Warm (>2°C)"
        if "cloud_cover" in wc and wc["cloud_cover"] is not None:
            c = float(wc["cloud_cover"])
            if c < 30:    bins["cloud_cover"] = "Clear (<30%)"
            elif c <= 70: bins["cloud_cover"] = "Partly Cloudy (30–70%)"
            else:         bins["cloud_cover"] = "Overcast (>70%)"
        if "precip_24h_mm" in wc and wc["precip_24h_mm"] is not None:
            p = float(wc["precip_24h_mm"])
            if p < 0.5:   bins["precipitation"] = "Dry (<0.5mm)"
            elif p <= 5:  bins["precipitation"] = "Light (0.5–5mm)"
            else:         bins["precipitation"] = "Heavy (>5mm)"
        if not bins:
            return 0.0, 0.0
        total, any_hit = 0.0, False
        for _cond, bin_label in bins.items():
            if bin_label not in ath_wp:
                continue
            avg_z, cnt = ath_wp[bin_label]
            shrunk = float(np.clip(cnt * avg_z / (cnt + wk), -WEATHER_CAP_PER, WEATHER_CAP_PER))
            total += shrunk
            any_hit = True
        if not any_hit:
            return 0.0, 0.0
        return float(np.clip(total, -WEATHER_CAP_TOT, WEATHER_CAP_TOT)), 1.0

    rows = []
    for _, sl_row in start_list.iterrows():
        fc   = str(sl_row["fis_code"]).strip()
        bib  = int(sl_row["bib"])
        name = str(sl_row.get("name", fc))

        ath = hist_df[hist_df["fis_code"] == fc].sort_values("date")
        fin = ath[ath["race_z_score"].notna()]
        vhist = fin[fin["location"].str.strip().str.lower() == venue.strip().lower()]

        n  = len(fin)
        zs = fin["race_z_score"].values.astype(float) if n > 0 else np.array([])

        if n >= 1:
            ewm_mid   = float(fin["race_z_score"].ewm(alpha=a,   adjust=True, ignore_na=True).mean().iloc[-1])
            ewm_short = float(fin["race_z_score"].ewm(alpha=a_s, adjust=True, ignore_na=True).mean().iloc[-1])
            ewm_long  = float(fin["race_z_score"].ewm(alpha=a_l, adjust=True, ignore_na=True).mean().iloc[-1])
        else:
            ewm_mid = ewm_short = ewm_long = 0.0
        ewm_shrunk = (n * ewm_mid) / (n + MEAN_K)

        roll3  = float(np.mean(zs[-3:]))  if n >= 1 else 0.0
        roll5  = float(np.mean(zs[-5:]))  if n >= 1 else 0.0
        roll10 = float(np.mean(zs[-10:])) if n >= 1 else 0.0
        _std_fill = _STD_FILL.get(discipline, 0.6)
        roll5_std = float(np.std(zs[-5:], ddof=1)) if n >= 2 else _std_fill

        fis_v = fin["fis_points"].values.astype(float) if n > 0 else np.array([35.0])
        fis_med = float(hist_df["fis_points"].median())
        roll5_fis  = float(np.mean(fis_v[-5:]))  if n >= 1 else fis_med
        roll10_fis = float(np.mean(fis_v[-10:])) if n >= 1 else fis_med

        last_z  = float(zs[-1]) if n >= 1 else 0.0
        last_rank = float(fin["actual_rank"].iloc[-1]) if n >= 1 else 99.0
        last_pod  = float(last_rank <= 3) if n >= 1 else 0.0
        last_win  = float(last_rank == 1) if n >= 1 else 0.0
        last_dnf  = float(ath["is_dnf"].iloc[-1]) if len(ath) > 0 else 0.0

        _dnf_fill = _DNF_RATE_FILL.get(discipline, 0.08)
        n_all       = len(ath)
        roll5_dnf   = float(ath.tail(5)["is_dnf"].mean())  if n_all > 0 else _dnf_fill
        career_dnf  = float(ath["is_dnf"].mean())           if n_all > 0 else _dnf_fill
        career_std  = float(np.std(zs, ddof=1))             if n >= 2 else _std_fill

        nwc = int(fin["actual_rank"].eq(1).sum())
        nw5 = int(fin.tail(5)["actual_rank"].eq(1).sum())
        nw10= int(fin.tail(10)["actual_rank"].eq(1).sum())
        npc = int((fin["actual_rank"] <= 3).sum())
        np5 = int((fin.tail(5)["actual_rank"] <= 3).sum())
        np10= int((fin.tail(10)["actual_rank"] <= 3).sum())
        nt10= int((fin.tail(10)["actual_rank"] <= 10).sum())

        wr_c = nwc / max(n, 1)
        wr5  = nw5 / max(min(n, 5), 1)
        pr5  = np5 / max(min(n, 5), 1)
        pr10 = np10 / max(min(n, 10), 1)
        prc  = npc / max(n, 1)
        t10  = nt10 / max(min(n, 10), 1)
        wcc  = wr_c / (prc + 1e-6)
        wc5  = wr5  / (pr5 + 1e-6)

        def _cend(arr, fn):
            c = 0
            for v in arr[::-1]:
                if not np.isnan(v) and fn(v): c += 1
                else: break
            return float(c)
        ar = fin["actual_rank"].values if n > 0 else np.array([])
        cstr = _cend(ar, lambda v: v <= 3)
        wstr = _cend(ar, lambda v: v == 1)

        cur_season = today.year if today.month >= 9 else today.year - 1
        yf = fin[fin["date"].apply(lambda d: d.year if d.month >= 9 else d.year - 1) == cur_season]
        sb = float(yf["race_z_score"].max()) if len(yf) > 0 else 0.0

        # WLS slope (from today backward)
        form_sl = 0.0
        if n >= 2:
            dates  = fin["date"].values
            ref    = np.datetime64(today.to_datetime64())
            past_d = ((ref - dates) / np.timedelta64(1, "D")).astype(float)
            valid  = ~np.isnan(zs)
            if valid.sum() >= 2:
                w   = np.exp(-lam * past_d[valid])
                z_  = zs[valid]; d_ = past_d[valid]
                W   = w.sum(); Wx = (w*d_).sum(); Wy = (w*z_).sum()
                Wxx = (w*d_*d_).sum(); Wxy = (w*d_*z_).sum()
                den = W*Wxx - Wx*Wx
                if abs(den) > 1e-10:
                    form_sl = -(W*Wxy - Wx*Wy) / den

        momentum = float(np.clip((last_z - ewm_mid) / (career_std + 0.1), -3, 3))
        cv_ratio  = career_std / (abs(ewm_shrunk) + 0.1)
        std_shrunk = career_std * ewm_shrunk      # v6: career_std * ewm_shrunk
        std_slope  = career_std * form_sl         # v6: career_std * form_slope

        _ds_fill = _DAYS_SINCE_FILL.get(discipline, 30.0)
        days_since = float((today - fin["date"].iloc[-1]).days) if n >= 1 else _ds_fill

        vn   = len(vhist)
        vraw = float(vhist["race_z_score"].mean()) if vn > 0 else ewm_shrunk
        vwin = float(vhist["actual_rank"].eq(1).mean()) if vn > 0 else 0.0
        vbst = float(vhist["race_z_score"].max())   if vn > 0 else ewm_shrunk
        vsh  = float((vn * vraw + v_k * ewm_shrunk) / (vn + v_k))

        bb_z  = float(bb_map.get(fc, 0.0))
        rdnf  = float(rdnf_map.get(fc, 0.0))
        bb_int = bb_z * last_dnf * max(ewm_shrunk, 0.0)
        ldnf_x_bb = last_dnf * bb_z
        rdnf_int  = last_dnf * rdnf

        w_adj, w_has = _weather_adj_for_athlete(fc)

        rows.append({
            "fis_code": fc, "name": name,
            "ewm_mean_z": ewm_mid,    "ewm_shrunk": ewm_shrunk,
            "ewm_short_z": ewm_short, "ewm_long_z": ewm_long,
            "roll3_mean_z": roll3,    "roll5_mean_z": roll5,
            "roll10_mean_z": roll10,  "roll5_std_z": roll5_std,
            "last_race_z": last_z,    "last_race_podium": last_pod,
            "last_race_win": last_win,"last_race_dnf": last_dnf,
            "roll5_dnf_rate": roll5_dnf, "career_dnf_rate": career_dnf,
            "career_std_z": career_std,  "cv_ratio": cv_ratio,
            "n_career": float(n),     "n_wins_career": float(nwc),
            "n_wins_5": float(nw5),   "n_wins_10": float(nw10),
            "win_rate_career": wr_c,  "win_rate_5": wr5,
            "podium_rate_5": pr5,     "podium_rate_10": pr10,
            "podium_rate_career": prc,"win_conv_career": wcc,
            "win_conv_5": wc5,        "consec_podiums": cstr,
            "consec_wins": wstr,      "top10_rate_10": t10,
            "season_best_z": sb,      "form_slope": form_sl,
            "momentum_z_norm": momentum, "rel_momentum": 0.0,
            "std_x_shrunk": std_shrunk,  "std_x_slope": std_slope,
            "bounce_back_z": bb_z,    "bounce_interact": bb_int,
            "re_dnf_rate": rdnf,      "last_dnf_x_bounce_z": ldnf_x_bb,
            "re_dnf_interact": rdnf_int,
            "venue_mean_z_raw": vraw, "venue_shrunk": vsh,
            "venue_n": float(vn),     "venue_win_rate": vwin,
            "venue_best_z": vbst,     "roll5_mean_fis": roll5_fis,
            "roll10_mean_fis": roll10_fis, "bib": float(bib),
            "days_since": days_since, "month": float(race_month),
            "weather_adj": w_adj,     "has_weather": w_has,
            "rel_ewm_z": 0.0,         "rel_venue_z": 0.0,
            "rel_fis": 0.0,           "rel_slope": 0.0,
            "rel_win_conv": 0.0,      "field_mean_z": 0.0,
            "field_std_z": 0.0,
        })

    pred_df = pd.DataFrame(rows)

    # Compute within-field relative features
    fm  = pred_df["ewm_shrunk"].mean()
    fs  = pred_df["ewm_shrunk"].std()
    vfm = pred_df["venue_shrunk"].mean()
    ffm = pred_df["roll5_mean_fis"].mean()
    sfm = pred_df["form_slope"].mean()
    wcm_f = pred_df["win_conv_career"].mean()
    mm  = pred_df["momentum_z_norm"].mean()

    pred_df["field_mean_z"]  = fm
    pred_df["field_std_z"]   = fs if not np.isnan(fs) else 0.0
    pred_df["rel_ewm_z"]     = pred_df["ewm_shrunk"]      - fm
    pred_df["rel_venue_z"]   = pred_df["venue_shrunk"]    - vfm
    pred_df["rel_fis"]       = pred_df["roll5_mean_fis"]  - ffm
    pred_df["rel_slope"]     = pred_df["form_slope"]      - sfm
    pred_df["rel_win_conv"]  = pred_df["win_conv_career"] - wcm_f
    pred_df["rel_momentum"]  = pred_df["momentum_z_norm"] - mm

    pred_df["pred_score"] = model.predict(pred_df[ALL_FEATURES].values.astype(float))
    pred_df = pred_df.sort_values("pred_score", ascending=False).reset_index(drop=True)
    pred_df.insert(0, "rank", range(1, len(pred_df) + 1))

    cols = [
        "rank", "bib", "fis_code", "name", "pred_score",
        "ewm_shrunk", "venue_shrunk", "venue_n", "form_slope",
        "weather_adj", "n_career",
    ]
    return pred_df[[c for c in cols if c in pred_df.columns]]


def list_venues(discipline: str, sex: str, race_type: str = "World Cup") -> list[str]:
    """Return sorted list of venue names for the given discipline/sex/race_type."""
    engine = get_engine()
    q = text("""
        SELECT DISTINCT location
        FROM raw.race_details
        WHERE discipline = :disc AND race_type = :rtype AND sex = :sex
        ORDER BY location
    """)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, params={"disc": discipline, "rtype": race_type, "sex": sex})
    return df["location"].tolist()
