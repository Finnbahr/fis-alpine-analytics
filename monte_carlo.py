"""
monte_carlo.py  —  Alpine Analytics race simulation engine
==========================================================

Reads from the same PostgreSQL database used by the ETL pipeline.
Run standalone for backtesting; eventually wired to the Race Simulator page.

Entry points
------------
run_simulation(start_list, discipline, ...)
    Monte Carlo prediction from a start list (DataFrame with bib + fis_code).

backtest_race(race_id, ...)
    Simulate a completed race using only data prior to it and score predictions.

backtest_range(discipline, race_type, n_races)
    Batch backtest — aggregate metrics across the last N races.

list_courses(discipline, min_races)
    Venues available for selection.

get_course_features(location, discipline, homologation_number)
    Feature dict for a specific course.

Architecture notes
------------------
- Two-run disciplines (Slalom, GS): Run 1 simulated from start bibs; Run 2 bibs
  reassigned per FIS rules (top-30 reversed, 31+ sequential, DNFs after all
  finishers by original bib). Final rank by combined time.
- DNF rate is split across two runs correctly:
      p_per_run = 1 - sqrt(1 - career_dnf_rate)
  so the combined probability equals the historical career rate.
- Time spread factor is fitted by OLS with intercept, not forced through origin.
  mean_winning_time from course_aggregate.basic_stats is stored in minutes.
- All ranking loops vectorised via np.argsort(argsort); bib reassignment is
  fully vectorised across all n_sims simultaneously.
- Historical data for any athlete load is filtered by race_type (default "World Cup")
  so World Cup distributions are not diluted by FIS/junior results.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime
import logging

from database import query as _q, get_engine
from sqlalchemy import text as _sqlt

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

N_SIMS            = 10_000
MOMENTUM_WEIGHT   = 0.15    # fraction of momentum_z applied to adjusted mean

# ── New feature constants (test) ──────────────────────────────────────────────
SLOPE_WEIGHT      = 14.0    # days — extrapolate trend 14 days forward
SLOPE_CAP         = 0.25    # max ±z contribution from form trajectory (fallback; see SLOPE_CAP_BY_DISC)
SLOPE_CAP_BY_DISC = {
    "Slalom":       0.25,   # SL: slope is strong signal; full ±0.25 cap confirmed by A/B
    "Giant Slalom": 0.10,   # GS: tighter cap — A/B: improves rho +0.016, MAE -1.17 vs disabled; no winner% loss
}
BOUNCE_WEIGHT     = 0.25    # weight on bounce_back_z_score when athlete had recent DNF
MEAN_SHRINK_K     = 3       # Bayesian shrinkage: wm_z * n/(n+K); K=3 → n=10 retains 77%, n=1 retains 25%

# CV-based std scaling: athletes with higher career std get wider simulation distributions.
# Uses std_race_z_score (all-time) relative to population median for the discipline.
CV_WEIGHT         = 0.4     # blending weight; 0 = no effect, 1 = full scale
CV_SCALE_MIN      = 0.80    # minimum std multiplier
CV_SCALE_MAX      = 1.35    # maximum std multiplier
CV_POP_STD        = {       # population median std_race_z_score (from performance_consistency_career, WC, ≥5 races)
    "Slalom":        0.67,
    "Giant Slalom":  0.66,
    "Super G":       0.70,
    "Downhill":      0.75,
}

# Per-discipline feature gates — controls which new signals are active.
_SLOPE_ENABLED  = {"Slalom", "Giant Slalom"}         # A/B: SL full cap; GS tighter cap=0.10 (see SLOPE_CAP_BY_DISC)
_BOUNCE_ENABLED = {"Slalom"}                        # bounce + re_dnf only for SL
_CV_ENABLED     = {"Slalom", "Giant Slalom"}        # testing CV std-scaling for both technical events
DECAY_HALFLIFE    = 180     # days; default recency-weighting half-life (overridden per discipline)
FIELD_SENSITIVITY = 0.15    # magnitude of field-quality z-adjustment (log scale)
MIN_COURSE_RACES  = 1       # minimum races at a venue to qualify for selection
DEFAULT_RACE_TYPE = "World Cup"
STD_FLOOR         = 0.40    # minimum weighted std_z (prevents over-certainty on few results)

# Per-discipline exponential decay half-life (days).
# SL form is ephemeral — weekly swings matter; use shorter halflife so recent races dominate.
# DH/SG venue specialists have multi-season track records — longer halflife preserves them.
DECAY_HALFLIFE_BY_DISC = {
    "Slalom":          120,   # form cycles fast; last 3-4 months most predictive
    "Giant Slalom":    180,   # medium stability; matches global default
    "Super G":         240,   # venue specialists carry form across seasons
    "Downhill":        270,   # strongest venue specialist effect; Mayer/Feuz patterns are multi-year
    "Alpine Combined": 180,
}

# Discipline normalisation: accept abbreviations or full names
_DISC_MAP = {
    "SL": "Slalom",
    "GS": "Giant Slalom",
    "SG": "Super G",
    "DH": "Downhill",
    "AC": "Alpine Combined",
}
TWO_RUN_FULL = {"Slalom", "Giant Slalom"}

# All race-type labels that represent a World Cup competition level.
# raw.race_details stores several distinct strings for the same tier.
_WC_TYPES = frozenset({
    "World Cup",
    "World Cup Speed Event",
    "Audi FIS Ski World Cup",
    "Olympic Winter Games",
    "World Championships",
})

# Traits stored in athlete_aggregate.course_regression
COURSE_TRAITS = ["gate_count", "start_altitude", "vertical_drop", "winning_time", "dnf_rate"]
BIB_TRAIT     = "bib"
_ALL_TRAITS   = COURSE_TRAITS + [BIB_TRAIT]

# Mapping: trait name → course_aggregate.basic_stats column name
HILL_COLS = {
    "gate_count":     "mean_gate_count",
    "start_altitude": "mean_start_altitude",
    "vertical_drop":  "mean_vertical_drop",
    "winning_time":   "mean_winning_time",   # stored in minutes
    "dnf_rate":       "mean_dnf_rate",
}

# Fallback spread factors when course-specific OLS fit is unavailable
SPREAD_DEFAULTS = {
    "Slalom":        0.012,
    "Giant Slalom":  0.010,
    "Super G":       0.015,
    "Downhill":      0.018,
}

# Per-discipline minimum std_z for athletes with real WC history.
# SL is genuinely volatile race-to-race; GS is more predictable (Odermatt dominance).
# Speed events have higher day-to-day variance.
STD_FLOOR_BY_DISC = {
    "Slalom":           0.38,   # floor is largely inert in SL (athletes' natural std exceeds it); 0.35–0.45 all yield identical rho
    "Giant Slalom":     0.50,   # Odermatt dominance is real; tighter floor lets the signal through
    "Super G":          0.60,   # single-run, higher day-to-day variance than GS
    "Downhill":         0.65,   # highest variance — single mistake changes everything
    "Alpine Combined":  0.50,
}

# Per-discipline maximum absolute course adjustment (z-score units).
# Speed events: course features are more discriminating (venue specific enough to trust ±1.2).
# Technical events: tighter cap because OLS fits are noisier.
COURSE_ADJ_CAP_BY_DISC = {
    "Slalom":          1.0,
    "Giant Slalom":    0.8,   # tighter cap reduces OLS noise from thin course histories
    "Super G":         0.8,   # tighter cap reduces OLS noise from thin venue histories
    "Downhill":        1.2,
    "Alpine Combined": 1.0,
}

# Per-discipline maximum absolute bib adjustment (z-score units).
# In two-run events bib matters (course deterioration for reversed run-2 starters).
# In single-run events bib effect is much weaker and less systematic.
BIB_ADJ_CAP_BY_DISC = {
    "Slalom":          0.6,
    "Giant Slalom":    0.6,
    "Super G":         0.25,  # bib has limited systematic effect in single-run events
    "Downhill":        0.25,
    "Alpine Combined": 0.4,
}

# Venue-specific advantage: Bayesian shrinkage constant k.
# venue_adj = (venue_mean_z - overall_mean_z) × n_venue / (n_venue + k)
# Higher k → slower to trust venue signal; lower k → faster to credit venue specialists.
# Speed events use smaller k (fewer starts per venue per athlete; trust sooner).
VENUE_SHRINKAGE_BY_DISC = {
    "Slalom":          5,    # many SL races per season; require 5 prior starts to reach 50% weight
    "Giant Slalom":    10,   # higher k → require more venue starts before trusting signal; reduces noise
    "Super G":         10,   # higher k → require more venue starts before trusting signal; reduces noise
    "Downhill":        10,   # DH venue specialists real, but need sufficient history to trust (Kitz/Bormio etc.)
    "Alpine Combined": 4,
}

# Maximum absolute venue-specific adjustment (z-score units).
# Speed events: stronger venue effects, higher cap.
VENUE_ADJ_CAP_BY_DISC = {
    "Slalom":          0.7,
    "Giant Slalom":    0.7,
    "Super G":         1.0,
    "Downhill":        1.0,
    "Alpine Combined": 0.7,
}

# Weather-condition adjustment.
# Applied when race-day weather is known (backtest: from raw.race_weather;
# live: user-supplied or Open-Meteo forecast).
# Bayesian shrinkage: weather_adj = (bucket_avg_z - overall_z) × n / (n + k)
# Tight cap — weather signal is real but weaker than venue specialisation and
# partially confounded with it (Kitzbühel is always cold; Kronplatz always clear).
#
# Per-discipline shrinkage k — technical events get lower k because:
#   - Venues rotate more (less weather-venue confounding)
#   - Athletes accumulate more starts per discipline
# Speed events use higher k: more starts per venue means venue adj captures some
# weather signal; but A/B tests show weather still improves rho for SG/DH.
WEATHER_SHRINKAGE_BY_DISC = {
    "Slalom":          20,   # k=20: winner 30.3% (≥30% target), top3 42.1%, rho 0.579
    "Giant Slalom":    50,   # k=50: winner 40.2% (≥40% target), top3 41.1%, rho 0.626
    "Super G":        500,   # k=500: winner 17.0% (ceiling on current race set), rho 0.629
    "Downhill":        50,   # k=50: winner 20.0% (≈20% target), rho 0.658
    "Alpine Combined": 30,
}
WEATHER_ADJ_CAP    = 0.20   # max abs adjustment per weather condition (σ units)
WEATHER_TOTAL_CAP  = 0.30   # max abs sum across all three conditions

# Std assigned to athletes whose z is estimated from FIS points only.
# Higher than any STD_FLOOR because point estimates carry genuine uncertainty.
_FIS_ESTIMATE_STD = 0.70

# Bib regression coefficients are fitted on run-1 bibs (range 1–7 for protected
# athletes like Odermatt). Applying them to run-2 reassigned bibs (range 1–30)
# is heavy extrapolation. Scale down to prevent run-2 bib from dominating.
_RUN2_BIB_SCALE   = 0.35


def _norm_disc(d: str) -> str:
    """Accept 'SL'/'GS'/… abbreviations or full discipline names."""
    return _DISC_MAP.get(d.upper().strip(), d.strip())


def _rt_filter(race_type: str) -> str:
    """
    Return a SQL snippet for the race_type column filter.

    If race_type is any WC-equivalent label, expands to an IN clause covering
    all known WC labels — so queries pick up 'Audi FIS Ski World Cup' rows
    alongside 'World Cup' rows.  Otherwise returns a simple equality test.

    Usage:  f"AND rd.race_type {_rt_filter(race_type)}"
    """
    if race_type in _WC_TYPES:
        wc_list = ", ".join(f"'{t}'" for t in sorted(_WC_TYPES))
        return f"IN ({wc_list})"
    return f"= '{race_type}'"


def _classify_weather_bucket(condition: str, value: float) -> str | None:
    """Map a raw weather value to the matching bucket label used in weather_performance."""
    if condition == "temperature":
        if value < -5.0:  return "Cold (<-5\u00b0C)"
        if value <  2.0:  return "Cool (-5\u20132\u00b0C)"
        return "Warm (>2\u00b0C)"
    if condition == "cloud_cover":
        if value < 30.0:  return "Clear (<30%)"
        if value <= 70.0: return "Partly Cloudy (30\u201370%)"
        return "Overcast (>70%)"
    if condition == "precipitation":
        if value <  0.5:  return "Dry (<0.5mm)"
        if value <= 5.0:  return "Light (0.5\u20135mm)"
        return "Heavy (>5mm)"
    return None


def _safe_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        return _q(sql, params)
    except Exception as exc:
        logger.warning("Query failed: %s", exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_recency_weighted_stats(
    fis_codes: list[str],
    discipline: str,
    race_type: str = DEFAULT_RACE_TYPE,
    reference_date: datetime | None = None,
    cutoff_date: str | None = None,
    sex: str | None = None,
) -> pd.DataFrame:
    """
    Compute exponentially decayed weighted mean and std of race_z_score per athlete.

    Parameters
    ----------
    cutoff_date : 'YYYY-MM-DD' — exclude races on or after this date.
                  Used for backtesting to prevent data leakage.
    sex         : "Men's" or "Women's" — filter historical data by gender.
                  Should always be supplied to avoid cross-gender contamination.
    """
    if reference_date is None:
        reference_date = datetime.now()

    codes_in    = ", ".join(f"'{c}'" for c in fis_codes)
    date_clause = f"AND rd.date < '{cutoff_date}'" if cutoff_date else ""
    sex_clause  = f"AND rd.sex = :sex" if sex else ""
    std_floor   = STD_FLOOR_BY_DISC.get(discipline, STD_FLOOR)

    params: dict = {"discipline": discipline}
    if sex:
        params["sex"] = sex

    df = _safe_query(f"""
        SELECT
            fr.fis_code::text            AS fis_code,
            rz.name,
            fr.fis_points::float         AS fis_points,
            rz.race_z_score::float       AS race_z_score,
            rd.date
        FROM raw.fis_results fr
        JOIN raw.race_details rd
            ON fr.race_id = rd.race_id
        JOIN race_aggregate.race_z_score rz
            ON fr.race_id = rz.race_id
           AND fr.fis_code = rz.fis_code
        WHERE fr.fis_code::text IN ({codes_in})
          AND rd.discipline  = :discipline
          AND rd.race_type   {_rt_filter(race_type)}
          AND fr.fis_points  IS NOT NULL
          AND rz.race_z_score IS NOT NULL
          {sex_clause}
          {date_clause}
    """, params)

    if df.empty:
        return _estimate_z_from_fis_points(fis_codes, discipline, sex, cutoff_date)

    df["date"]         = pd.to_datetime(df["date"], errors="coerce")
    df["fis_points"]   = pd.to_numeric(df["fis_points"],   errors="coerce")
    df["race_z_score"] = pd.to_numeric(df["race_z_score"], errors="coerce")
    df = df.dropna(subset=["date", "race_z_score", "fis_points"])

    if df.empty:
        return _estimate_z_from_fis_points(fis_codes, discipline, sex, cutoff_date)

    halflife = DECAY_HALFLIFE_BY_DISC.get(discipline, DECAY_HALFLIFE)
    lam = np.log(2) / halflife
    df["days_ago"] = (reference_date - df["date"]).dt.days.clip(lower=0)
    df["weight"]   = np.exp(-lam * df["days_ago"])

    rows = []
    for code, grp in df.groupby("fis_code"):
        w  = grp["weight"].values
        z  = grp["race_z_score"].values
        fp = grp["fis_points"].values

        if len(grp) >= 2:
            wm_z  = float(np.average(z,  weights=w))
            wm_fp = float(np.average(fp, weights=w))
            wstd  = float(np.sqrt(np.average((z - wm_z) ** 2, weights=w)))
        else:
            wm_z  = float(z[0])
            wm_fp = float(fp[0])
            wstd  = 1.0

        # Form trajectory slope: regression of z on days_ago (weighted).
        # Positive slope → older races worse → athlete improving recently.
        # Requires 3+ races to be meaningful; clipped at ±SLOPE_CAP when applied.
        slope = 0.0
        if len(grp) >= 3:
            d = grp["days_ago"].values.astype(float)
            w_norm = w / w.sum()
            d_bar = float(np.dot(w_norm, d))
            z_bar = wm_z
            cov_dz = float(np.dot(w_norm, (d - d_bar) * (z - z_bar)))
            var_d  = float(np.dot(w_norm, (d - d_bar) ** 2))
            if var_d > 1e-9:
                slope = cov_dz / var_d   # z-score per day

        rows.append({
            "fis_code":              code,
            "name":                  grp["name"].iloc[0],
            "weighted_mean_z":       round(wm_z,  4),
            "weighted_std_z":        round(max(wstd, std_floor), 4),
            "weighted_mean_fis":     round(wm_fp, 2),
            "race_count_discipline": len(grp),
            "form_slope":            round(slope, 7),
        })

    stats = pd.DataFrame(rows).set_index("fis_code")
    missing = [c for c in fis_codes if c not in stats.index]
    if missing:
        stats = pd.concat([stats, _estimate_z_from_fis_points(
            missing, discipline, sex, cutoff_date)])
    return stats


def _default_stats(fis_codes: list[str]) -> pd.DataFrame:
    """
    Last-resort placeholders for athletes with no FIS points data at all.
    mean_z = -1.0: well-below-average prior (unknown athlete = not WC-competitive).
    std_z  = 0.70: uncertain but not a lottery ticket.
    """
    if not fis_codes:
        return pd.DataFrame(columns=[
            "fis_code", "name", "weighted_mean_z",
            "weighted_std_z", "weighted_mean_fis", "race_count_discipline",
        ])
    rows = [{
        "fis_code":              c,
        "name":                  c,          # caller should overwrite with start-list name
        "weighted_mean_z":       -1.0,       # well-below-average prior (no FIS data → not WC-competitive)
        "weighted_std_z":        0.70,       # uncertain but not lottery-wide
        "weighted_mean_fis":     50.0,
        "race_count_discipline": 0,
    } for c in fis_codes]
    return pd.DataFrame(rows).set_index("fis_code")


def _estimate_z_from_fis_points(
    fis_codes: list[str],
    discipline: str,
    sex: str | None = None,
    cutoff_date: str | None = None,
) -> pd.DataFrame:
    """
    For athletes with no WC z-score history, estimate performance from FIS points.

    Uses the empirical OLS relationship (fit on all WC starters):
        z ≈ intercept + slope × fis_points

    which has correlation –0.74 to –0.89 depending on discipline.
    Falls back to _default_stats for athletes whose FIS points are also unavailable.
    """
    if not fis_codes:
        return _default_stats(fis_codes)

    # Fit the FIS→z regression from known WC history (same discipline + sex)
    sex_clause  = "AND rd.sex = :sex" if sex else ""
    reg_params: dict = {"discipline": discipline}
    if sex:
        reg_params["sex"] = sex

    reg = _safe_query(f"""
        SELECT
            REGR_SLOPE(rz.race_z_score, fr.fis_points)     AS slope,
            REGR_INTERCEPT(rz.race_z_score, fr.fis_points) AS intercept
        FROM raw.fis_results fr
        JOIN raw.race_details rd ON fr.race_id = rd.race_id
        JOIN race_aggregate.race_z_score rz
            ON fr.race_id = rz.race_id AND fr.fis_code = rz.fis_code
        WHERE rd.race_type {_rt_filter(DEFAULT_RACE_TYPE)}
          AND rd.discipline = :discipline
          AND fr.fis_points IS NOT NULL
          AND rz.race_z_score IS NOT NULL
          {sex_clause}
    """, reg_params)

    if reg.empty or pd.isna(reg.iloc[0]["slope"]):
        return _default_stats(fis_codes)

    slope     = float(reg.iloc[0]["slope"])
    intercept = float(reg.iloc[0]["intercept"])

    # Get each athlete's most recent FIS points in the discipline (any race level)
    codes_in    = ", ".join(f"'{c}'" for c in fis_codes)
    date_clause = f"AND rd.date < '{cutoff_date}'" if cutoff_date else ""

    fp_df = _safe_query(f"""
        SELECT DISTINCT ON (fr.fis_code::text)
            fr.fis_code::text    AS fis_code,
            fr.name,
            fr.fis_points::float AS fis_points
        FROM raw.fis_results fr
        JOIN raw.race_details rd ON fr.race_id = rd.race_id
        WHERE fr.fis_code::text IN ({codes_in})
          AND rd.discipline = :discipline
          AND fr.fis_points IS NOT NULL
          AND fr.fis_points::float >= 0.0
          {date_clause}
        ORDER BY fr.fis_code::text, rd.date DESC
    """, {"discipline": discipline})

    rows = []
    found = set()

    if not fp_df.empty:
        for _, row in fp_df.iterrows():
            code  = str(row["fis_code"])
            fp    = float(row["fis_points"])
            if pd.isna(fp):
                continue        # PostgreSQL NaN float slipped through — treat as missing
            est_z = float(np.clip(intercept + slope * fp, -2.5, -0.3))
            rows.append({
                "fis_code":              code,
                "name":                  str(row.get("name", code) or code),
                "weighted_mean_z":       round(est_z, 4),
                "weighted_std_z":        _FIS_ESTIMATE_STD,
                "weighted_mean_fis":     round(fp, 2),
                "race_count_discipline": 0,
            })
            found.add(code)

    # Truly unknown — no FIS points anywhere for this discipline
    still_missing = [c for c in fis_codes if c not in found]
    if still_missing:
        fallback = _default_stats(still_missing)
        if rows:
            known_df = pd.DataFrame(rows).set_index("fis_code")
            return pd.concat([known_df, fallback])
        return fallback

    return pd.DataFrame(rows).set_index("fis_code")


def load_course_regression_coefficients(
    fis_codes: list[str],
    discipline: str,
) -> pd.DataFrame:
    """
    Per-athlete course-trait regression coefficients from
    athlete_aggregate.course_regression.
    Returns zero coefficients for athletes not in the table.
    """
    codes_in  = ", ".join(f"'{c}'" for c in fis_codes)
    traits_in = ", ".join(f"'{t}'" for t in _ALL_TRAITS)

    df = _safe_query(f"""
        SELECT fis_code, trait, coefficient
        FROM athlete_aggregate.course_regression
        WHERE fis_code   IN ({codes_in})
          AND discipline = :discipline
          AND trait      IN ({traits_in})
    """, {"discipline": discipline})

    if df.empty:
        return pd.DataFrame(0.0, index=fis_codes, columns=_ALL_TRAITS)

    pivot = (
        df.pivot_table(index="fis_code", columns="trait",
                       values="coefficient", aggfunc="first")
          .reindex(columns=_ALL_TRAITS, fill_value=0.0)
          .fillna(0.0)
    )
    return pivot.reindex(fis_codes, fill_value=0.0).fillna(0.0)


def load_dnf_rates(
    fis_codes: list[str],
    discipline: str,
    race_type: str = DEFAULT_RACE_TYPE,
) -> pd.Series:
    """
    Career DNF rate per athlete from performance_consistency_career.
    Filtered to race_type to avoid mixing WC and FIS rates.
    Defaults to 8% for athletes not found.
    """
    codes_in = ", ".join(f"'{c}'" for c in fis_codes)

    df = _safe_query(f"""
        SELECT fis_code, dnf_rate
        FROM athlete_aggregate.performance_consistency_career
        WHERE fis_code  IN ({codes_in})
          AND discipline = :discipline
          AND race_type  {_rt_filter(race_type)}
    """, {"discipline": discipline})

    s = pd.Series(0.08, index=fis_codes, name="dnf_rate")
    if not df.empty:
        # Average across any duplicate rows (same fis_code can appear under
        # multiple WC-equivalent race_type labels).
        deduped = df.groupby("fis_code")["dnf_rate"].mean()
        s.update(deduped.astype(float))
    return s


def load_consistency_extras(
    fis_codes: list[str],
    discipline: str,
    race_type: str = DEFAULT_RACE_TYPE,
) -> pd.DataFrame:
    """
    Load re_dnf_rate, bounce_back_z_score, cv_race_z, std_race_z_score per athlete.
    Returns DataFrame indexed by fis_code; missing athletes get neutral defaults.
    """
    codes_in = ", ".join(f"'{c}'" for c in fis_codes)
    df = _safe_query(f"""
        SELECT fis_code, re_dnf_rate, bounce_back_z_score, cv_race_z, std_race_z_score
        FROM athlete_aggregate.performance_consistency_career
        WHERE fis_code  IN ({codes_in})
          AND discipline = :discipline
          AND race_type  {_rt_filter(race_type)}
    """, {"discipline": discipline})

    defaults = pd.DataFrame({
        "re_dnf_rate":         0.08,
        "bounce_back_z_score": 0.0,
        "cv_race_z":           0.5,
        "std_race_z_score":    float("nan"),   # NaN → no CV scaling applied
    }, index=fis_codes)
    defaults.index.name = "fis_code"

    if not df.empty:
        cols = ["re_dnf_rate", "bounce_back_z_score", "cv_race_z", "std_race_z_score"]
        deduped = df.groupby("fis_code")[cols].mean()
        defaults.update(deduped.astype(float))
    return defaults


def _load_recent_dnf_flag(
    fis_codes: list[str],
    discipline: str,
    sex: str | None,
    cutoff_date: str | None,
) -> dict[str, bool]:
    """
    Returns {fis_code: True} if the athlete's most recent race in this discipline
    before cutoff_date was a DNF or DSQ. Used to switch to re_dnf_rate.
    """
    if not fis_codes:
        return {}
    codes_in    = ", ".join(f"'{c}'" for c in fis_codes)
    date_clause = f"AND rd.date < '{cutoff_date}'" if cutoff_date else ""
    sex_clause  = "AND rd.sex = :sex" if sex else ""
    params: dict = {"discipline": discipline}
    if sex:
        params["sex"] = sex

    df = _safe_query(f"""
        SELECT DISTINCT ON (fr.fis_code)
            fr.fis_code::text AS fis_code,
            fr.rank
        FROM raw.fis_results fr
        JOIN raw.race_details rd ON rd.race_id = fr.race_id
        WHERE fr.fis_code::text IN ({codes_in})
          AND rd.discipline = :discipline
          AND rd.race_type  {_rt_filter(DEFAULT_RACE_TYPE)}
          {sex_clause}
          {date_clause}
        ORDER BY fr.fis_code, rd.date DESC
    """, params)

    result: dict[str, bool] = {}
    if not df.empty:
        for _, row in df.iterrows():
            r = str(row["rank"]).upper()
            result[str(row["fis_code"])] = r.startswith("DNF") or r.startswith("DSQ")
    return result


def load_momentum(
    fis_codes: list[str],
    discipline: str,
    cutoff_date: str | None = None,
) -> pd.Series:
    """
    Most recent momentum_z per athlete from hot_streak (all race types combined).
    cutoff_date: exclude entries on or after this date — required for backtesting.
    Defaults to 0.0 (neutral) for athletes not found.
    """
    codes_in    = ", ".join(f"'{c}'" for c in fis_codes)
    date_clause = f"AND date < '{cutoff_date}'" if cutoff_date else ""

    df = _safe_query(f"""
        SELECT DISTINCT ON (fis_code)
            fis_code,
            momentum_z
        FROM athlete_aggregate.hot_streak
        WHERE fis_code   IN ({codes_in})
          AND discipline = :discipline
          AND momentum_z IS NOT NULL
          {date_clause}
        ORDER BY fis_code, date DESC
    """, {"discipline": discipline})

    s = pd.Series(0.0, index=fis_codes, name="momentum_z")
    if not df.empty:
        s.update(df.set_index("fis_code")["momentum_z"].astype(float))
    return s


def load_venue_specific_advantage(
    fis_codes: list[str],
    location: str,
    discipline: str,
    sex: str | None,
    cutoff_date: str | None,
    overall_means: pd.Series,   # weighted_mean_z per fis_code (from load_recency_weighted_stats)
    reference_date: datetime | None = None,
) -> pd.Series:
    """
    Estimate each athlete's venue-specific advantage using Bayesian shrinkage.

    For each athlete, compute their recency-weighted mean z-score at this specific
    location and compare it to their overall discipline mean. The difference is
    shrunk toward zero based on how few venue starts exist:

        venue_adj = (venue_mean_z − overall_mean_z) × n_venue / (n_venue + k)

    where k = VENUE_SHRINKAGE_BY_DISC[discipline].

    This captures athlete × venue specificity that general course-feature regression
    cannot — e.g. Mayer always outperforming at Kitzbühel, Feuz at Lauberhorn,
    Gut-Behrami at Kronplatz — independent of gate count or vertical drop.

    Returns a Series of venue_adj values indexed by fis_code (0.0 for athletes
    with no prior starts at this venue).
    """
    if not location or not fis_codes:
        return pd.Series(0.0, index=fis_codes, name="venue_adj")

    codes_in    = ", ".join(f"'{c}'" for c in fis_codes)
    date_clause = f"AND rd.date < '{cutoff_date}'" if cutoff_date else ""
    sex_clause  = "AND rd.sex = :sex" if sex else ""

    params: dict = {"location": location, "discipline": discipline}
    if sex:
        params["sex"] = sex

    df = _safe_query(f"""
        SELECT
            fr.fis_code::text      AS fis_code,
            rz.race_z_score::float AS race_z_score,
            rd.date
        FROM raw.fis_results fr
        JOIN raw.race_details rd
            ON fr.race_id = rd.race_id
        JOIN race_aggregate.race_z_score rz
            ON fr.race_id = rz.race_id
           AND fr.fis_code = rz.fis_code
        WHERE fr.fis_code::text IN ({codes_in})
          AND rd.location    = :location
          AND rd.discipline  = :discipline
          AND rd.race_type   {_rt_filter(DEFAULT_RACE_TYPE)}
          AND rz.race_z_score IS NOT NULL
          {sex_clause}
          {date_clause}
    """, params)

    result = pd.Series(0.0, index=fis_codes, name="venue_adj")
    if df.empty:
        return result

    df["date"]         = pd.to_datetime(df["date"], errors="coerce")
    df["race_z_score"] = pd.to_numeric(df["race_z_score"], errors="coerce")
    df = df.dropna(subset=["date", "race_z_score"])
    if df.empty:
        return result

    k   = VENUE_SHRINKAGE_BY_DISC.get(discipline, 4)
    cap = VENUE_ADJ_CAP_BY_DISC.get(discipline, 0.7)

    # Use the same halflife as the overall model for this discipline
    halflife = DECAY_HALFLIFE_BY_DISC.get(discipline, DECAY_HALFLIFE)
    lam      = np.log(2) / halflife
    ref_date = reference_date if reference_date is not None else datetime.now()
    df["days_ago"] = (ref_date - df["date"]).dt.days.clip(lower=0)
    df["weight"]   = np.exp(-lam * df["days_ago"])

    adjs = {}
    for code, grp in df.groupby("fis_code"):
        w = grp["weight"].values
        z = grp["race_z_score"].values
        n_venue  = len(grp)
        venue_wm = float(np.average(z, weights=w))
        overall  = float(overall_means.get(code, 0.0))
        shrinkage_weight = n_venue / (n_venue + k)
        raw_adj  = (venue_wm - overall) * shrinkage_weight
        adjs[str(code)] = float(np.clip(raw_adj, -cap, cap))

    result.update(pd.Series(adjs))
    return result


def load_weather_advantage(
    fis_codes: list[str],
    weather: dict,
    discipline: str,
    overall_means: pd.Series,
) -> pd.Series:
    """
    Per-athlete weather-condition advantage via Bayesian shrinkage.

    Parameters
    ----------
    weather : dict with any of:
        air_temp_c      — race-day mean temperature (°C)
        cloud_cover     — race-day mean cloud cover (%)
        precip_24h_mm   — race-day precipitation (mm)
    overall_means : weighted_mean_z per fis_code from load_recency_weighted_stats.

    Mechanism
    ---------
    For each condition with a non-null race-day value:
      1. Classify into the matching bucket (e.g. "Cool (-5–2°C)").
      2. Look up each athlete's avg_z_score in that bucket
         from athlete_aggregate.weather_performance.
      3. Compute excess = (bucket_avg_z − athlete_overall_z).
      4. Shrink: adj = excess × n_bucket / (n_bucket + WEATHER_SHRINKAGE).
      5. Cap each condition at ±WEATHER_ADJ_CAP.
    Sum across conditions, then cap total at ±WEATHER_TOTAL_CAP.

    Returns a Series of weather_adj values indexed by fis_code (0.0 for athletes
    with no weather history in that bucket).
    """
    result = pd.Series(0.0, index=fis_codes, name="weather_adj")
    if not weather or not fis_codes:
        return result

    # Map raw values → bucket labels for each condition
    raw_map = {
        "temperature":   weather.get("air_temp_c"),
        "cloud_cover":   weather.get("cloud_cover"),
        "precipitation": weather.get("precip_24h_mm"),
    }
    active: dict[str, str] = {}
    for cond, val in raw_map.items():
        if val is not None:
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if np.isnan(fval):
                continue
            bucket = _classify_weather_bucket(cond, fval)
            if bucket:
                active[cond] = bucket

    if not active:
        return result

    logger.debug("Weather buckets: %s", active)

    codes_in  = ", ".join(f"'{c}'" for c in fis_codes)
    cond_in   = ", ".join(f"'{c}'" for c in active.keys())
    bucket_in = ", ".join(f"'{b}'" for b in active.values())
    wc_types  = ", ".join(f"'{t}'" for t in sorted(_WC_TYPES))

    # Filter to WC race types and aggregate across any that are stored separately
    # (e.g. 'World Cup' and 'Audi FIS Ski World Cup' are both valid WC labels).
    # This prevents FIS/junior bucket averages from contaminating WC predictions.
    df = _safe_query(f"""
        SELECT
            fis_code::text AS fis_code,
            condition,
            condition_bin,
            SUM(avg_z_score * race_count) / NULLIF(SUM(race_count), 0) AS avg_z_score,
            SUM(race_count)                                              AS race_count
        FROM athlete_aggregate.weather_performance
        WHERE fis_code::text IN ({codes_in})
          AND discipline      = :discipline
          AND condition       IN ({cond_in})
          AND condition_bin   IN ({bucket_in})
          AND race_type       IN ({wc_types})
        GROUP BY fis_code::text, condition, condition_bin
    """, {"discipline": discipline})

    if df.empty:
        return result

    k   = WEATHER_SHRINKAGE_BY_DISC.get(discipline, 30)
    cap = WEATHER_ADJ_CAP

    adjs: dict[str, float] = {}
    for code in fis_codes:
        athlete_rows = df[df["fis_code"] == code]
        overall_z    = float(overall_means.get(code, 0.0))
        total_adj    = 0.0

        for cond, bucket in active.items():
            match = athlete_rows[
                (athlete_rows["condition"] == cond) &
                (athlete_rows["condition_bin"] == bucket)
            ]
            if match.empty:
                continue
            bucket_z  = float(match.iloc[0]["avg_z_score"])
            n_bucket  = int(match.iloc[0]["race_count"])
            shrinkage = n_bucket / (n_bucket + k)
            raw_adj   = (bucket_z - overall_z) * shrinkage
            total_adj += float(np.clip(raw_adj, -cap, cap))

        adjs[code] = float(np.clip(total_adj, -WEATHER_TOTAL_CAP, WEATHER_TOTAL_CAP))

    result.update(pd.Series(adjs))
    return result


# ---------------------------------------------------------------------------
# Course / hill data
# ---------------------------------------------------------------------------

def get_course_features(
    location: str,
    discipline: str,
    homologation_number: str,
) -> dict:
    """
    Return feature dict from course_aggregate.basic_stats.
    Returns empty dict if the course doesn't meet the minimum race count.

    Note: mean_winning_time is in minutes (as stored in basic_stats).

    Lookup order:
      1. Exact homologation number (most precise).
      2. Location-only fallback — picks the homologation at that venue with
         the most historical races. Handles races where the homologation
         number is missing from the scrape but the venue is well-known.
    """
    # 1. Primary: exact homologation match
    if homologation_number:
        df = _safe_query("""
            SELECT mean_gate_count, mean_start_altitude, mean_vertical_drop,
                   mean_winning_time, mean_dnf_rate, race_count
            FROM course_aggregate.basic_stats
            WHERE location            = :loc
              AND discipline          = :disc
              AND homologation_number = :hom
        """, {"loc": location, "disc": discipline, "hom": homologation_number})

        if not df.empty and int(df.iloc[0].get("race_count", 0)) >= MIN_COURSE_RACES:
            row = df.iloc[0]
            return {
                trait: float(row[hill_col])
                for trait, hill_col in HILL_COLS.items()
                if hill_col in df.columns and pd.notna(row[hill_col])
            }

    # 2. Location-only fallback (homologation missing or not in basic_stats)
    df = _safe_query("""
        SELECT mean_gate_count, mean_start_altitude, mean_vertical_drop,
               mean_winning_time, mean_dnf_rate, race_count
        FROM course_aggregate.basic_stats
        WHERE location   = :loc
          AND discipline = :disc
          AND race_count >= :min_r
        ORDER BY race_count DESC
        LIMIT 1
    """, {"loc": location, "disc": discipline, "min_r": MIN_COURSE_RACES})

    if df.empty:
        return {}

    row = df.iloc[0]
    return {
        trait: float(row[hill_col])
        for trait, hill_col in HILL_COLS.items()
        if hill_col in df.columns and pd.notna(row[hill_col])
    }


def load_setter_features(
    setter_name: str,
    discipline: str,
    cutoff_date: str | None = None,
) -> dict:
    """
    Return averaged course features for all WC races set by `setter_name`
    in `discipline` before `cutoff_date`.

    Used as a fallback when get_course_features() returns {} (no homologation
    data on record). A setter who consistently sets high-gate, high-DNF courses
    carries that signature into venues where we have no historical data.

    Returns empty dict if the setter has fewer than 2 qualifying races.
    """
    params: dict = {"setter": setter_name, "disc": discipline}
    cutoff_clause = ""
    if cutoff_date:
        cutoff_clause = "AND rd.date < :cutoff"
        params["cutoff"] = cutoff_date

    rows = _safe_query(f"""
        SELECT cs.mean_gate_count, cs.mean_start_altitude, cs.mean_vertical_drop,
               cs.mean_winning_time, cs.mean_dnf_rate
        FROM raw.race_details rd
        JOIN course_aggregate.basic_stats cs
            ON cs.homologation_number = rd.homologation_number
           AND cs.discipline          = rd.discipline
        WHERE rd.first_run_course_setter = :setter
          AND rd.discipline = :disc
          AND rd.race_type IN (
                'World Cup', 'Audi FIS Ski World Cup',
                'Olympic Winter Games', 'World Championships'
              )
          {cutoff_clause}
    """, params)

    if rows.empty or len(rows) < 2:
        return {}

    return {
        trait: float(rows[hill_col].mean())
        for trait, hill_col in HILL_COLS.items()
        if hill_col in rows.columns and rows[hill_col].notna().any()
    }


def get_discipline_population_means(discipline: str) -> dict:
    """
    Discipline-level mean for each course feature.
    Used to centre course adjustments so a typical course yields zero adjustment.
    """
    df = _safe_query("""
        SELECT mean_gate_count, mean_start_altitude, mean_vertical_drop,
               mean_winning_time, mean_dnf_rate
        FROM course_aggregate.basic_stats
        WHERE discipline = :disc AND race_count >= :min_r
    """, {"disc": discipline, "min_r": MIN_COURSE_RACES})

    if df.empty:
        return {t: 0.0 for t in HILL_COLS}

    return {
        trait: float(df[hill_col].mean())
        for trait, hill_col in HILL_COLS.items()
        if hill_col in df.columns
    }


def compute_time_spread_factor(
    location: str,
    discipline: str,
    homologation_number: str,
) -> tuple[float, float]:
    """
    Fit OLS (with intercept) relating time-gap fractions to z-scores at a course.

    Returns (spread_factor, time_bias) where:
        gap_fraction ≈ spread_factor × (−z) + time_bias

    Falls back to (discipline default, 0.0) if fewer than 10 data points.
    Using OLS with intercept (not through-origin) is more correct because
    the winner's z-score is not zero, so the intercept captures the average
    gap for a z=0 (median) athlete.
    """
    default = (SPREAD_DEFAULTS.get(discipline, 0.012), 0.0)

    df = _safe_query("""
        SELECT fr.race_id,
               fr.final_time,
               fr.rank,
               rz.race_z_score::float AS race_z_score
        FROM raw.fis_results fr
        JOIN raw.race_details rd
            ON fr.race_id = rd.race_id
        JOIN race_aggregate.race_z_score rz
            ON fr.race_id = rz.race_id
           AND fr.fis_code = rz.fis_code
        WHERE rd.location            = :loc
          AND rd.discipline          = :disc
          AND rd.homologation_number = :hom
          AND fr.final_time IS NOT NULL
          AND fr.final_time != ''
          AND rz.race_z_score IS NOT NULL
    """, {"loc": location, "disc": discipline, "hom": homologation_number})

    if df.empty:
        return default

    def _to_sec(t) -> float | None:
        try:
            s = str(t).strip()
            if not s or s in ("nan", "None", ""):
                return None
            if ":" in s:
                m, rest = s.split(":", 1)
                return float(m) * 60 + float(rest)
            return float(s)
        except Exception:
            return None

    df["time_sec"]     = df["final_time"].apply(_to_sec)
    df["race_z_score"] = pd.to_numeric(df["race_z_score"], errors="coerce")
    df = df.dropna(subset=["time_sec", "race_z_score"])

    gap_fracs, z_vals = [], []
    for _, grp in df.groupby("race_id"):
        winner = grp[grp["rank"] == "1"]
        if winner.empty:
            continue
        wt = float(winner["time_sec"].iloc[0])
        if wt <= 0:
            continue
        for _, row in grp.iterrows():
            gap = (float(row["time_sec"]) - wt) / wt
            if gap >= 0:
                gap_fracs.append(gap)
                z_vals.append(float(row["race_z_score"]))

    if len(gap_fracs) < 10:
        return default

    neg_z = -np.array(z_vals)
    gf    = np.array(gap_fracs)

    # OLS with intercept: gf = spread * neg_z + bias
    X = np.column_stack([neg_z, np.ones(len(neg_z))])
    result = np.linalg.lstsq(X, gf, rcond=None)
    spread = float(result[0][0])
    bias   = float(result[0][1])

    return max(round(spread, 6), 0.001), round(max(bias, 0.0), 6)


def list_courses(
    discipline: str | None = None,
    min_races: int = MIN_COURSE_RACES,
) -> pd.DataFrame:
    """Return all courses meeting the minimum race count."""
    disc_clause = "AND discipline = :disc" if discipline else ""
    params = {"min_r": min_races}
    if discipline:
        params["disc"] = discipline
    return _safe_query(f"""
        SELECT location, homologation_number, discipline, country, race_count
        FROM course_aggregate.basic_stats
        WHERE race_count >= :min_r {disc_clause}
        ORDER BY location
    """, params)


# ---------------------------------------------------------------------------
# Field quality adjustment
# ---------------------------------------------------------------------------

def compute_field_quality_adjustment(
    athlete_stats: pd.DataFrame,
    start_list: pd.DataFrame,
) -> pd.Series:
    """
    Adjust each athlete's base z-score for field quality.

    Mechanism: compare the average FIS points of the uploaded field against
    each athlete's historical field quality. Stronger-than-usual field → lower
    expected z. FIELD_SENSITIVITY controls magnitude.
    """
    fis_codes = start_list["fis_code"].astype(str).tolist()
    known     = [c for c in fis_codes if c in athlete_stats.index]

    if not known:
        return pd.Series(0.0, index=fis_codes)

    fis_vals = [float(athlete_stats.loc[c, "weighted_mean_fis"]) for c in known]
    field_q  = float(np.nanmean(fis_vals))
    if np.isnan(field_q):
        return pd.Series(0.0, index=fis_codes)

    adj = {}
    for code in fis_codes:
        if code not in athlete_stats.index:
            adj[code] = 0.0
            continue
        hist_q = float(athlete_stats.loc[code, "weighted_mean_fis"])
        if not np.isfinite(hist_q) or hist_q <= 0:
            adj[code] = 0.0
            continue
        # Log ratio: weaker field (higher avg FIS pts) vs athlete's own pts → boost z
        # Log prevents explosion when elite athletes (FIS pts ~0) vs average WC field
        ratio     = field_q / hist_q
        adj[code] = round(float(np.log(max(ratio, 1e-6))) * FIELD_SENSITIVITY, 4)

    return pd.Series(adj, name="field_quality_adj")


# ---------------------------------------------------------------------------
# Parameter assembly
# ---------------------------------------------------------------------------

def assemble_adjusted_params(
    fis_codes: list[str],
    start_list: pd.DataFrame,
    discipline: str,
    course_features: dict,
    athlete_stats: pd.DataFrame,
    regression: pd.DataFrame,
    dnf_rates: pd.Series,
    momentum: pd.Series,
    field_adj: pd.Series,
    population_means: dict,
    venue_adj: pd.Series | None = None,
    weather_adj: pd.Series | None = None,
    consistency_extras: pd.DataFrame | None = None,
    recent_dnf_flags: dict | None = None,
) -> pd.DataFrame:
    """
    Compute final adjusted mean z-score and std for every athlete.

    adjusted_mean = base_mean
                  + field_quality_adj
                  + Σ coef_i × (course_feature_i − discipline_pop_mean_i)
                  + bib_coef × start_bib
                  + momentum_z × MOMENTUM_WEIGHT
                  + venue_specific_advantage   (Bayesian shrinkage toward athlete's overall mean)
                  + weather_condition_advantage (Bayesian shrinkage, capped ±0.5 total)
    """
    rows = []
    for code in fis_codes:
        if code in athlete_stats.index:
            base_mean  = float(athlete_stats.loc[code, "weighted_mean_z"])
            base_std   = float(athlete_stats.loc[code, "weighted_std_z"])
            race_count = int(athlete_stats.loc[code, "race_count_discipline"])
            stats_name = str(athlete_stats.loc[code, "name"])
        else:
            base_mean, base_std, race_count = -0.5, 0.70, 0
            stats_name = code

        # Always prefer the start-list name — it's the most authoritative source.
        # Fall back to the stats name only if the start list doesn't have one.
        name_match = start_list.loc[start_list["fis_code"] == code, "name"]
        if len(name_match) and str(name_match.values[0]).strip():
            name = str(name_match.values[0])
        else:
            name = stats_name if stats_name != code else code

        bib_match = start_list.loc[start_list["fis_code"] == code, "bib"]
        bib = int(bib_match.values[0]) if len(bib_match) else 99

        # Course adjustment (5 feature traits, population-mean centred).
        # Skip entirely when course features are unavailable (empty dict) to avoid
        # applying regression against placeholder zeros far from population means.
        # Cap at ±1.5: speed events in particular have sparse per-venue history
        # so OLS coefficients overfit; a tighter cap limits damage.
        course_adj = 0.0
        if course_features and code in regression.index:
            for trait in COURSE_TRAITS:
                if trait not in regression.columns:
                    continue
                coef  = float(regression.loc[code, trait])
                val   = float(course_features.get(trait, 0.0))
                pop   = float(population_means.get(trait, val))
                course_adj += coef * (val - pop)
            cap = COURSE_ADJ_CAP_BY_DISC.get(discipline, 1.0)
            course_adj = float(np.clip(course_adj, -cap, cap))

        # Bib adjustment — separate channel from course traits.
        # Two-run events: bib matters (course deterioration for run-2 late starters) → cap ±0.6.
        # Single-run events: bib has weak systematic effect → tighter cap.
        bib_adj = (
            float(regression.loc[code, BIB_TRAIT]) * bib
            if code in regression.index and BIB_TRAIT in regression.columns
            else 0.0
        )
        bib_cap = BIB_ADJ_CAP_BY_DISC.get(discipline, 0.6)
        bib_adj = float(np.clip(bib_adj, -bib_cap, bib_cap))

        mom_adj       = float(momentum.get(code, 0.0)) * MOMENTUM_WEIGHT
        fq_adj        = float(field_adj.get(code, 0.0))
        venue_adj_val   = float(venue_adj.get(code, 0.0))   if venue_adj   is not None else 0.0
        weather_adj_val = float(weather_adj.get(code, 0.0)) if weather_adj is not None else 0.0

        # Form slope: positive slope (dz/d_days_ago > 0) means older races were worse
        # → athlete improving → subtract from predicted z (lower z = better in our convention).
        # Only applied for disciplines where slope carries real signal (technical events).
        if discipline in _SLOPE_ENABLED:
            form_slope = (
                float(athlete_stats.loc[code, "form_slope"])
                if code in athlete_stats.index and "form_slope" in athlete_stats.columns
                else 0.0
            )
            cap = SLOPE_CAP_BY_DISC.get(discipline, SLOPE_CAP)
            slope_adj = float(np.clip(-form_slope * SLOPE_WEIGHT, -cap, cap))
        else:
            slope_adj = 0.0

        # Recent DNF: switch to conditional re-DNF rate; apply bounce-back momentum.
        # Only applied for disciplines where the signal is reliable (SL).
        had_recent_dnf = bool((recent_dnf_flags or {}).get(code, False))
        if discipline in _BOUNCE_ENABLED and had_recent_dnf \
                and consistency_extras is not None and code in consistency_extras.index:
            dnf_rate_val = float(consistency_extras.loc[code, "re_dnf_rate"])
            bb_z         = float(consistency_extras.loc[code, "bounce_back_z_score"])
            bounce_adj   = float(bb_z * BOUNCE_WEIGHT)
        else:
            dnf_rate_val = float(dnf_rates.get(code, 0.08))
            bounce_adj   = 0.0

        # CV-based std scaling: compare career std_race_z_score to discipline population median.
        # Athletes more volatile than average get a wider simulation distribution.
        # NaN career_std → no scaling (defaults to base_std as-is).
        cv_scale = 1.0
        if discipline in _CV_ENABLED and consistency_extras is not None \
                and code in consistency_extras.index:
            career_std = float(consistency_extras.loc[code, "std_race_z_score"])
            if not np.isnan(career_std):
                pop_std = CV_POP_STD.get(discipline, 0.67)
                raw_scale = float(np.clip(career_std / pop_std, CV_SCALE_MIN, CV_SCALE_MAX))
                cv_scale  = 1.0 + CV_WEIGHT * (raw_scale - 1.0)
        sim_std = base_std * cv_scale

        rows.append({
            "fis_code":              code,
            "name":                  name,
            "bib":                   bib,
            "adjusted_mean":         round(base_mean + fq_adj + course_adj + bib_adj + mom_adj
                                           + venue_adj_val + weather_adj_val + slope_adj + bounce_adj, 4),
            "base_std":              round(sim_std, 4),
            "dnf_rate":              dnf_rate_val,
            # Breakdown columns for transparency / debugging
            "base_mean":             round(base_mean, 4),
            "course_adj":            round(course_adj, 4),
            "bib_adj":               round(bib_adj, 4),
            "momentum_adj":          round(mom_adj, 4),
            "field_adj":             round(fq_adj, 4),
            "venue_adj":             round(venue_adj_val, 4),
            "weather_adj":           round(weather_adj_val, 4),
            "slope_adj":             round(slope_adj, 4),
            "bounce_adj":            round(bounce_adj, 4),
            "had_recent_dnf":        had_recent_dnf,
            "cv_scale":              round(cv_scale, 4),
            "race_count_discipline": race_count,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Bib reassignment — fully vectorised across all simulations
# ---------------------------------------------------------------------------

def _reassign_run2_bibs(
    ranks1:    np.ndarray,   # (n_sims, n)  1-based; DNF athletes have rank n+1
    dnf1:      np.ndarray,   # (n_sims, n)  bool
    orig_bibs: np.ndarray,   # (n,)         original start-list bibs
) -> np.ndarray:
    """
    FIS Run 2 bib assignment — vectorised across all n_sims simultaneously.

    Rules:
      Finishers ranked 1–30: reversed — rank-1 gets bib n_top30, rank-n_top30 gets bib 1.
      Finishers ranked 31+:  sequential — rank-31 gets bib 31, rank-32 gets bib 32, …
      DNFs: bibs after all finishers, ordered by original bib number.

    DNF bibs don't affect simulation results (those athletes are masked out of
    combined_time by np.inf), but are assigned correctly for output completeness.
    """
    n_fin   = (~dnf1).sum(axis=1, keepdims=True)   # (n_sims, 1)
    n_top30 = np.minimum(n_fin, 30)                 # (n_sims, 1)

    in_top30 = (~dnf1) & (ranks1 <= n_top30)
    in_rest  = (~dnf1) & (ranks1 > n_top30)

    run2_bibs = np.zeros_like(ranks1)
    # Top-30: reverse order (rank-1 → n_top30, rank-n_top30 → 1)
    run2_bibs = np.where(in_top30, n_top30 + 1 - ranks1, run2_bibs)
    # 31+: sequential
    run2_bibs = np.where(in_rest, ranks1, run2_bibs)

    # DNFs: bibs n_fin+1, n_fin+2, … in orig_bib order (same order every sim)
    bib_order     = np.argsort(orig_bibs)           # (n,)  — static across sims
    inv_bib_order = np.argsort(bib_order)           # (n,)
    dnf_sorted    = dnf1[:, bib_order]              # (n_sims, n)
    dnf_rank      = np.cumsum(dnf_sorted, axis=1)[:, inv_bib_order]   # (n_sims, n)
    run2_bibs     = np.where(dnf1, n_fin + dnf_rank, run2_bibs)

    return run2_bibs


# ---------------------------------------------------------------------------
# Simulation engines
# ---------------------------------------------------------------------------

def _rank_array(scores: np.ndarray, dnf_mask: np.ndarray, n: int) -> np.ndarray:
    """
    Rank athletes by score (descending) within each simulation, vectorised.
    DNF athletes receive rank n+1 regardless of their drawn score.
    """
    scores_ranked = np.where(dnf_mask, -np.inf, scores)
    order = np.argsort(-scores_ranked, axis=1)      # (n_sims, n)
    ranks = np.argsort(order, axis=1) + 1           # vectorised inverse permutation
    return np.where(dnf_mask, n + 1, ranks)


def _run_single(
    params: pd.DataFrame,
    n_sims: int,
    rng: np.random.Generator,
) -> dict:
    """Simulate a single-run event (DH, SG, Alpine Combined)."""
    means     = params["adjusted_mean"].values
    stds      = params["base_std"].values
    dnf_probs = params["dnf_rate"].values
    n         = len(params)

    z    = rng.normal(loc=means, scale=stds, size=(n_sims, n))
    dnf  = rng.random(size=(n_sims, n)) < dnf_probs
    ranks = _rank_array(z, dnf, n)

    return {"z": z, "dnf": dnf, "ranks": ranks}


def _run_two(
    params: pd.DataFrame,
    regression: pd.DataFrame,
    mean_winning_time: float,   # combined winning time in minutes (from basic_stats)
    spread_factor: float,
    time_bias: float,           # OLS intercept from compute_time_spread_factor
    n_sims: int,
    rng: np.random.Generator,
) -> dict:
    """
    Simulate a two-run event (Slalom, Giant Slalom) with full FIS bib reassignment.

    DNF rate is split correctly across both runs:
        p_per_run = 1 - sqrt(1 - career_dnf_rate)
    so the combined probability matches the historical career rate.

    Time formula (used for ranking by combined time):
        time_per_run = (mean_winning_time / 2) × (1 + time_bias + spread_factor × (−z))
    mean_winning_time/2 is the per-run approximation; units are minutes but
    only relative order matters for ranking so the scale is irrelevant.
    """
    n          = len(params)
    fis_codes  = params["fis_code"].values
    orig_bibs  = params["bib"].values.astype(int)
    means_r1   = params["adjusted_mean"].values
    stds       = params["base_std"].values
    career_dnf = params["dnf_rate"].values

    # Split career DNF rate into per-run probability
    per_run_dnf = 1.0 - np.sqrt(np.maximum(0.0, 1.0 - career_dnf))

    # Bib regression coefficient per athlete
    bib_coefs = np.array([
        float(regression.loc[c, BIB_TRAIT])
        if c in regression.index and BIB_TRAIT in regression.columns else 0.0
        for c in fis_codes
    ])

    # Base mean without bib component (so Run 2 bib can be substituted cleanly)
    base_no_bib = means_r1 - bib_coefs * orig_bibs

    per_run_time = mean_winning_time / 2.0

    # ── Run 1 ────────────────────────────────────────────────────────────
    z1    = rng.normal(loc=means_r1, scale=stds, size=(n_sims, n))
    dnf1  = rng.random(size=(n_sims, n)) < per_run_dnf
    ranks1 = _rank_array(z1, dnf1, n)

    time1 = per_run_time * (1.0 + time_bias + spread_factor * (-z1))
    time1 = np.where(dnf1, np.inf, time1)

    # ── Bib reassignment — fully vectorised ──────────────────────────────
    run2_bibs = _reassign_run2_bibs(ranks1, dnf1, orig_bibs)          # (n_sims, n)
    # Scale bib_coefs for run 2: the coefficient was fitted on run-1 bibs
    # (range ≈ 1–7 for elite athletes). Applying it to run-2 reassigned bibs
    # (range 1–30) extrapolates far outside the training range and over-penalises
    # top athletes who go last in run 2.
    means_r2  = base_no_bib[np.newaxis, :] + (bib_coefs * _RUN2_BIB_SCALE)[np.newaxis, :] * run2_bibs

    # ── Run 2 ────────────────────────────────────────────────────────────
    z2        = rng.normal(loc=means_r2, scale=stds[np.newaxis, :], size=(n_sims, n))
    dnf2_only = rng.random(size=(n_sims, n)) < per_run_dnf
    comb_dnf  = dnf1 | dnf2_only

    time2 = per_run_time * (1.0 + time_bias + spread_factor * (-z2))
    time2 = np.where(comb_dnf, np.inf, time2)

    comb_time = time1 + time2

    # Final rank by combined time (vectorised)
    order_f  = np.argsort(comb_time, axis=1)
    ranks_f  = np.argsort(order_f, axis=1) + 1
    ranks_f  = np.where(comb_dnf, n + 1, ranks_f)

    return {
        "run1_z":        z1,
        "run1_dnf":      dnf1,
        "run1_ranks":    ranks1,
        "run2_z":        z2,
        "run2_dnf_only": dnf2_only,
        "combined_dnf":  comb_dnf,
        "run2_bibs":     run2_bibs,
        "combined_time": comb_time,
        "final_ranks":   ranks_f,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate(params: pd.DataFrame, sim: dict, is_two_run: bool) -> pd.DataFrame:
    """Aggregate Monte Carlo arrays into per-athlete probabilities."""
    ranks    = sim["final_ranks"] if is_two_run else sim["ranks"]
    dnf_mask = sim["combined_dnf"] if is_two_run else sim["dnf"]
    n        = len(params)

    # DNF-inclusive expected rank: DNF outcomes count as n+1.
    # Used for display sorting so high-DNF athletes don't appear ahead of reliable finishers.
    r_all    = np.where(~dnf_mask, ranks.astype(float), float(n + 1))
    exp_rank = r_all.mean(axis=0)
    med_rank = np.median(r_all, axis=0)

    # Conditional expected rank: mean finishing position among non-DNF sims only.
    # Used for backtest Spearman rho (which evaluates athletes who actually finished).
    r_fin      = np.where(~dnf_mask, ranks.astype(float), np.nan)
    with np.errstate(all="ignore"):
        cond_rank = np.nanmean(r_fin, axis=0)
    cond_rank = np.where(np.isnan(cond_rank), float(n + 1), cond_rank)
    p_win    = (ranks == 1).mean(axis=0)
    p_podium = (ranks <= 3).mean(axis=0)
    p_top10  = (ranks <= 10).mean(axis=0)
    p_dnf    = dnf_mask.mean(axis=0)

    rows = []
    for idx, (_, row) in enumerate(params.iterrows()):
        d = {
            "fis_code":    row["fis_code"],
            "name":        row["name"],
            "bib":         int(row["bib"]),
            "p_win":       round(float(p_win[idx])    * 100, 1),
            "p_podium":    round(float(p_podium[idx]) * 100, 1),
            "p_top10":     round(float(p_top10[idx])  * 100, 1),
            "p_dnf":       round(float(p_dnf[idx])    * 100, 1),
            "expected_rank":
                round(float(exp_rank[idx]), 1) if not np.isnan(exp_rank[idx]) else float(n + 1),
            "conditional_rank":
                round(float(cond_rank[idx]), 1),
            "median_rank":
                int(round(float(med_rank[idx]))) if not np.isnan(med_rank[idx]) else n + 1,
            "adjusted_mean_z":       row["adjusted_mean"],
            "base_mean_z":           row["base_mean"],
            "course_adj":            row["course_adj"],
            "bib_adj":               row["bib_adj"],
            "momentum_adj":          row["momentum_adj"],
            "field_adj":             row["field_adj"],
            "venue_adj":             row.get("venue_adj", 0.0),
            "weather_adj":           row.get("weather_adj", 0.0),
            "race_count_discipline": int(row["race_count_discipline"]),
        }
        if is_two_run:
            d["p_dnf_run1"]    = round(float(sim["run1_dnf"][:, idx].mean())      * 100, 1)
            d["p_dnf_run2"]    = round(float(sim["run2_dnf_only"][:, idx].mean()) * 100, 1)
            d["mean_run2_bib"] = round(float(sim["run2_bibs"][:, idx].mean()),    1)
        rows.append(d)

    return (
        pd.DataFrame(rows)
        .sort_values(["p_win", "expected_rank"], ascending=[False, True])
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_simulation(
    start_list: pd.DataFrame,
    discipline: str,
    race_type: str = DEFAULT_RACE_TYPE,
    location: str | None = None,
    homologation_number: str | None = None,
    course_features: dict | None = None,
    setter_name: str | None = None,
    n_sims: int = N_SIMS,
    random_seed: int | None = 42,
    reference_date: datetime | None = None,
    cutoff_date: str | None = None,
    sex: str | None = None,
    weather_conditions: dict | None = None,
) -> pd.DataFrame:
    """
    Run the Monte Carlo race simulation.

    Parameters
    ----------
    start_list         : DataFrame with columns bib (int), fis_code (str),
                         optionally name (str).
    discipline         : Full name ('Slalom') or abbreviation ('SL').
    race_type          : Historical data filter (default 'World Cup').
    location           : Venue name — used for course feature lookup.
    homologation_number: FIS homologation — used for course features + spread factor.
    course_features    : Pre-built feature dict; if None, loaded from DB.
    n_sims             : Number of Monte Carlo iterations.
    random_seed        : Integer for reproducibility; None = random each run.
    cutoff_date        : 'YYYY-MM-DD' — exclude historical data on/after this date
                         (use for backtesting to prevent leakage).
    weather_conditions : dict with any of {air_temp_c, cloud_cover, precip_24h_mm}.
                         If provided, applies per-athlete weather adjustment.
                         In backtest_race() this is loaded automatically from raw.race_weather.

    Returns
    -------
    DataFrame sorted by p_win (desc) with one row per athlete:
        fis_code, name, bib, p_win, p_podium, p_top10, p_dnf,
        expected_rank, median_rank,
        adjusted_mean_z, base_mean_z, course_adj, bib_adj, momentum_adj, field_adj,
        race_count_discipline,
        [p_dnf_run1, p_dnf_run2, mean_run2_bib]  — two-run events only
    """
    rng        = np.random.default_rng(random_seed)
    discipline = _norm_disc(discipline)
    two_run    = discipline in TWO_RUN_FULL
    fis_codes  = start_list["fis_code"].astype(str).tolist()

    logger.info(
        "Simulation | discipline=%s | athletes=%d | n_sims=%d | two_run=%s",
        discipline, len(fis_codes), n_sims, two_run,
    )

    # Course features — try homologation data first, then setter history as fallback
    if course_features is None and location and homologation_number:
        course_features = get_course_features(location, discipline, homologation_number)
    if not course_features and setter_name:
        course_features = load_setter_features(setter_name, discipline, cutoff_date)
        if course_features:
            logger.info("No venue data — using setter '%s' history as course proxy.", setter_name)
    if not course_features:
        logger.warning("No course features available — course adjustments will be zero.")
        course_features = {}

    population_means  = get_discipline_population_means(discipline)
    spread_factor     = SPREAD_DEFAULTS.get(discipline, 0.012)
    time_bias         = 0.0
    mean_winning_time = float(course_features.get("winning_time", 1.5))  # minutes

    if two_run and location and homologation_number:
        spread_factor, time_bias = compute_time_spread_factor(
            location, discipline, homologation_number)
        logger.info(
            "Spread factor=%.4f | time_bias=%.4f | mean_winning_time=%.3f min",
            spread_factor, time_bias, mean_winning_time,
        )

    # Athlete data
    stats      = load_recency_weighted_stats(fis_codes, discipline, race_type,
                                             reference_date, cutoff_date, sex)
    regression = load_course_regression_coefficients(fis_codes, discipline)
    dnf_rates  = load_dnf_rates(fis_codes, discipline, race_type)
    momentum   = load_momentum(fis_codes, discipline, cutoff_date)
    field_adj  = compute_field_quality_adjustment(stats, start_list)

    # Venue-specific advantage — Bayesian shrinkage of (venue_mean_z - overall_mean_z).
    # Only computed when we know the location (always the case in backtest and live sim).
    venue_adv = load_venue_specific_advantage(
        fis_codes, location or "", discipline, sex, cutoff_date,
        overall_means=stats["weighted_mean_z"] if not stats.empty else pd.Series(dtype=float),
        reference_date=reference_date,
    ) if location else None

    # Weather adjustment — per-athlete bucket delta vs their overall baseline.
    # Only applied when race-day weather is known (auto-populated in backtest).
    overall_means_s = stats["weighted_mean_z"] if not stats.empty else pd.Series(dtype=float)
    weather_adv = load_weather_advantage(
        fis_codes, weather_conditions, discipline, overall_means_s,
    ) if weather_conditions else None
    if weather_adv is not None:
        nonzero = (weather_adv != 0).sum()
        logger.info("Weather adjustment applied: %d athletes adjusted.", nonzero)

    # New signals: consistency extras (re_dnf_rate, bounce_back_z, cv_race_z)
    # and recent-DNF flag (last race was DNF/DSQ → switch to conditional DNF rate).
    consistency_extras = load_consistency_extras(fis_codes, discipline, race_type)
    recent_dnf_flags   = _load_recent_dnf_flag(fis_codes, discipline, sex, cutoff_date)

    params = assemble_adjusted_params(
        fis_codes, start_list, discipline, course_features,
        stats, regression, dnf_rates, momentum, field_adj, population_means,
        venue_adj=venue_adv,
        weather_adj=weather_adv,
        consistency_extras=consistency_extras,
        recent_dnf_flags=recent_dnf_flags,
    )

    logger.info("Parameters assembled. Running simulation...")

    if two_run:
        sim = _run_two(params, regression, mean_winning_time,
                       spread_factor, time_bias, n_sims, rng)
    else:
        sim = _run_single(params, n_sims, rng)

    results = _aggregate(params, sim, two_run)

    top3 = " | ".join(
        f"{r['name']} {r['p_win']}%"
        for _, r in results.head(3).iterrows()
    )
    logger.info("Done. Top 3 p_win: %s", top3)
    return results


# ---------------------------------------------------------------------------
# Backtesting
# ---------------------------------------------------------------------------

def backtest_race(
    race_id: int,
    race_type: str = DEFAULT_RACE_TYPE,
    n_sims: int = N_SIMS,
    random_seed: int | None = None,
) -> dict:
    """
    Simulate a completed race using only data that existed before it.

    Returns
    -------
    dict with keys:
        'predictions'  — simulation output DataFrame (with 'actual_rank' column added)
        'metrics'      — dict of accuracy statistics
    """
    meta = _safe_query("""
        SELECT location, discipline, homologation_number,
               race_type, date::text AS date, sex,
               first_run_course_setter
        FROM raw.race_details
        WHERE race_id = :rid
    """, {"rid": race_id})

    if meta.empty:
        logger.warning("Race %d not found.", race_id)
        return {}

    m          = meta.iloc[0]
    discipline = str(m["discipline"])
    location   = str(m["location"])
    hom        = str(m["homologation_number"]) if pd.notna(m["homologation_number"]) else ""
    date_str   = str(m["date"])[:10]
    # Use the race's actual stored race_type for historical data queries.
    actual_race_type = str(m["race_type"]) if pd.notna(m.get("race_type")) else race_type
    # Filter historical data to the same gender as this race.
    race_sex   = str(m["sex"]) if pd.notna(m.get("sex")) else None
    # Course setter — used as course-feature proxy when no homologation data exists.
    setter     = str(m["first_run_course_setter"]) if pd.notna(m.get("first_run_course_setter")) else None
    if setter == "":
        setter = None

    # Build start list from the stored results
    start_df = _safe_query("""
        SELECT bib, fis_code::text AS fis_code, name
        FROM raw.fis_results
        WHERE race_id = :rid AND bib IS NOT NULL
    """, {"rid": race_id})

    if start_df.empty:
        logger.warning("No result rows for race %d.", race_id)
        return {}

    # Race-day weather — loaded from raw.race_weather if available
    weather_row = _safe_query("""
        SELECT air_temp_c, cloud_cover, precip_24h_mm
        FROM raw.race_weather
        WHERE race_id = :rid
        LIMIT 1
    """, {"rid": race_id})
    weather_conditions = weather_row.iloc[0].to_dict() if not weather_row.empty else None

    predictions = run_simulation(
        start_list=start_df,
        discipline=discipline,
        race_type=actual_race_type,
        location=location,
        homologation_number=hom,
        setter_name=setter,
        n_sims=n_sims,
        random_seed=random_seed,
        cutoff_date=date_str,   # no data on or after race day
        sex=race_sex,
        weather_conditions=weather_conditions,
    )

    # Actual results
    actual = _safe_query("""
        SELECT fis_code::text AS fis_code, rank
        FROM raw.fis_results
        WHERE race_id = :rid
    """, {"rid": race_id})

    actual["rank_int"] = pd.to_numeric(actual["rank"], errors="coerce")
    actual = actual.dropna(subset=["rank_int"])
    actual["rank_int"] = actual["rank_int"].astype(int)
    actual_map = actual.set_index("fis_code")["rank_int"].to_dict()

    predictions["actual_rank"] = (
        predictions["fis_code"]
        .map(actual_map)
        .fillna(len(start_df) + 1)
        .astype(int)
    )

    n_starters = len(start_df)
    matched = predictions[predictions["actual_rank"] <= n_starters]

    if len(matched) < 5:
        metrics = {"race_id": race_id, "note": "too few finishers for metrics"}
    else:
        try:
            from scipy.stats import spearmanr
            rho, _ = spearmanr(matched["conditional_rank"], matched["actual_rank"])
            rho    = round(float(rho), 3)
        except ImportError:
            rho = None

        mae = round(float(
            (matched["conditional_rank"] - matched["actual_rank"]).abs().mean()
        ), 2)

        actual_top3 = set(actual.loc[actual["rank_int"] <= 3, "fis_code"])
        pred_top3   = set(predictions.head(3)["fis_code"])
        top3_recall = round(len(actual_top3 & pred_top3) / max(len(actual_top3), 1), 3)

        actual_winner = actual.loc[actual["rank_int"] == 1, "fis_code"]
        winner_correct = (
            len(actual_winner) > 0
            and actual_winner.iloc[0] == predictions.iloc[0]["fis_code"]
        )

        metrics = {
            "race_id":       race_id,
            "discipline":    discipline,
            "location":      location,
            "race_date":     date_str,
            "n_starters":    n_starters,
            "spearman_rho":  rho,
            "rank_mae":      mae,
            "top3_recall":   top3_recall,
            "winner_correct": winner_correct,
        }

    logger.info(
        "Backtest race %d (%s %s %s) | rho=%s | MAE=%.2f | winner=%s",
        race_id, discipline, location, date_str,
        metrics.get("spearman_rho", "n/a"),
        metrics.get("rank_mae", 0),
        metrics.get("winner_correct", "?"),
    )
    return {"predictions": predictions, "metrics": metrics}


def backtest_range(
    discipline: str,
    race_type: str = DEFAULT_RACE_TYPE,
    n_races: int = 20,
    n_sims: int = 2_000,
) -> pd.DataFrame:
    """
    Backtest the last n_races for a given discipline and race_type.

    Returns a DataFrame of per-race accuracy metrics. Use n_sims=2_000 for
    speed during exploratory backtesting; increase to 10_000 for final results.
    """
    discipline = _norm_disc(discipline)

    races = _safe_query("""
        SELECT DISTINCT rd.race_id
        FROM raw.race_details rd
        JOIN race_aggregate.race_z_score rz ON rz.race_id = rd.race_id
        WHERE rd.discipline = :disc
          AND rd.race_type  = :rt
        ORDER BY rd.race_id DESC
        LIMIT :n
    """, {"disc": discipline, "rt": race_type, "n": n_races})

    if races.empty:
        logger.warning("No races found for %s / %s", discipline, race_type)
        return pd.DataFrame()

    all_metrics = []
    for race_id in races["race_id"]:
        result = backtest_race(int(race_id), race_type=race_type, n_sims=n_sims)
        if result and "metrics" in result and "spearman_rho" in result["metrics"]:
            all_metrics.append(result["metrics"])

    return pd.DataFrame(all_metrics)


# ---------------------------------------------------------------------------
# Save simulation results to PostgreSQL
# ---------------------------------------------------------------------------

def save_simulation(
    results: pd.DataFrame,
    discipline: str,
    course_label: str,
    race_id: int | None = None,
) -> None:
    """Persist simulation output to simulation.mc_results."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(_sqlt("""
            CREATE SCHEMA IF NOT EXISTS simulation;
            CREATE TABLE IF NOT EXISTS simulation.mc_results (
                id            SERIAL PRIMARY KEY,
                race_id       INTEGER,
                discipline    TEXT,
                course_label  TEXT,
                simulated_at  TIMESTAMP,
                fis_code      TEXT,
                name          TEXT,
                bib           INTEGER,
                p_win         NUMERIC,
                p_podium      NUMERIC,
                p_top10       NUMERIC,
                p_dnf         NUMERIC,
                expected_rank NUMERIC,
                median_rank   INTEGER,
                adjusted_mean_z NUMERIC,
                base_mean_z   NUMERIC,
                course_adj    NUMERIC,
                bib_adj       NUMERIC,
                momentum_adj  NUMERIC,
                field_adj     NUMERIC
            );
        """))

    out = results.copy()
    out["discipline"]   = _norm_disc(discipline)
    out["course_label"] = course_label
    out["race_id"]      = race_id
    out["simulated_at"] = datetime.now()

    keep = [
        "race_id", "discipline", "course_label", "simulated_at",
        "fis_code", "name", "bib", "p_win", "p_podium", "p_top10",
        "p_dnf", "expected_rank", "median_rank",
        "adjusted_mean_z", "base_mean_z", "course_adj",
        "bib_adj", "momentum_adj", "field_adj",
    ]
    out = out[[c for c in keep if c in out.columns]]
    out.to_sql("mc_results", get_engine(), schema="simulation",
               if_exists="append", index=False)
    logger.info("Saved %d rows to simulation.mc_results.", len(out))


# ---------------------------------------------------------------------------
# CLI — quick smoke test or batch backtest
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # python monte_carlo.py backtest GS 15
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        disc  = sys.argv[2] if len(sys.argv) > 2 else "GS"
        n     = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        rt    = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_RACE_TYPE
        print(f"\nBacktesting last {n} {disc} '{rt}' races (2,000 sims each)...\n")
        df = backtest_range(disc, race_type=rt, n_races=n, n_sims=2_000)
        if not df.empty:
            print(df.to_string(index=False))
            print(f"\nMean Spearman rho  : {df['spearman_rho'].mean():.3f}")
            print(f"Mean rank MAE      : {df['rank_mae'].mean():.2f}")
            print(f"Top-3 recall       : {df['top3_recall'].mean():.1%}")
            print(f"Winner accuracy    : {df['winner_correct'].mean():.1%}")
        sys.exit(0)

    # python monte_carlo.py race 130500
    if len(sys.argv) > 1 and sys.argv[1] == "race":
        rid = int(sys.argv[2])
        print(f"\nBacktesting race_id {rid}...\n")
        result = backtest_race(rid, n_sims=5_000)
        if result:
            print(result["predictions"][
                ["name", "bib", "p_win", "p_podium", "p_top10",
                 "p_dnf", "expected_rank", "actual_rank"]
            ].to_string(index=False))
            print("\nMetrics:", result["metrics"])
        sys.exit(0)

    # Default: smoke test with a small hand-built start list
    test_list = pd.DataFrame({
        "bib":      [1,        2,        3,        4,        5,        6       ],
        "fis_code": ["512182", "422729", "511983", "54445",  "202451", "6531444"],
        "name":     ["Odermatt","Braathen","Meillard","Schwarz","Pinturault","Ryan"],
    })

    print("\n── Single-run smoke test (SG, 2,000 sims) ──")
    res = run_simulation(test_list, "SG", n_sims=2_000, random_seed=1)
    print(res[["name", "bib", "p_win", "p_podium", "p_top10",
               "p_dnf", "expected_rank"]].to_string(index=False))

    print("\n── Two-run smoke test (GS, 2,000 sims) ──")
    res2 = run_simulation(test_list, "GS", n_sims=2_000, random_seed=1)
    print(res2[["name", "bib", "p_win", "p_podium", "p_top10",
                "p_dnf", "expected_rank", "mean_run2_bib"]].to_string(index=False))
