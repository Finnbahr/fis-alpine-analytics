"""
Athletes API endpoints.
"""

from fastapi import APIRouter, HTTPException, Query, Path
from typing import Optional
import logging

from app.database import execute_query, execute_query_single
from app.models import (
    AthleteListResponse,
    AthleteListItem,
    AthleteProfile,
    AthleteCareerStats,
    AthleteTier,
    AthleteMomentum,
    AthleteRaceHistoryResponse,
    AthleteRaceHistoryItem,
    MomentumResponse,
    MomentumDataPoint,
    CoursePerformanceResponse,
    CoursePerformanceItem,
    StrokesGainedResponse,
    StrokesGainedItem,
    StrokesGainedBibResponse,
    StrokesGainedBibItem,
    RegressionResponse,
    RegressionCoefficient,
    CourseTraitResponse,
    CourseTraitQuintileItem,
    PaginationMeta,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/athletes", response_model=AthleteListResponse)
def list_athletes(
    name: Optional[str] = Query(None, description="Search by athlete name (case-insensitive)"),
    country: Optional[str] = Query(None, description="Filter by country code (e.g., 'USA')"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    tier: Optional[str] = Query(None, description="Filter by performance tier"),
    limit: int = Query(50, ge=1, le=500, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """
    List or search athletes.

    - **name**: Search by athlete name (partial match, case-insensitive)
    - **country**: Filter by country code
    - **discipline**: Filter by discipline
    - **tier**: Filter by tier (Elite, Contender, Middle, Developing)
    - **limit**: Results per page (max 500)
    - **offset**: Pagination offset
    """
    logger.info(f"GET /athletes - name={name}, country={country}, discipline={discipline}, tier={tier}")

    # Build query based on filters
    if name:
        # Search by name
        query = """
            SELECT DISTINCT fis_code, name, country
            FROM raw.fis_results
            WHERE LOWER(name) LIKE LOWER(%(name)s)
            LIMIT %(limit)s OFFSET %(offset)s
        """
        params = {"name": f"%{name}%", "limit": limit, "offset": offset}
        results = execute_query(query, params)

        athletes = [
            AthleteListItem(
                fis_code=row["fis_code"],
                name=row["name"],
                country=row.get("country")
            )
            for row in results
        ]

    elif tier or discipline:
        # Filter by tier and/or discipline
        query = """
            SELECT DISTINCT
                pt.fis_code,
                pt.name,
                pt.discipline,
                pt.tier,
                pt.race_count,
                pt.avg_fis_points,
                aic.starts,
                aic.wins,
                aic.podiums
            FROM athlete_aggregate.performance_tiers pt
            LEFT JOIN athlete_aggregate.basic_athlete_info_career aic
                ON pt.fis_code = aic.fis_code
            WHERE 1=1
        """
        params = {"limit": limit, "offset": offset}

        if tier:
            query += " AND pt.tier = %(tier)s"
            params["tier"] = tier

        if discipline:
            query += " AND pt.discipline = %(discipline)s"
            params["discipline"] = discipline

        query += " ORDER BY pt.avg_fis_points LIMIT %(limit)s OFFSET %(offset)s"

        results = execute_query(query, params)

        athletes = [
            AthleteListItem(
                fis_code=row["fis_code"],
                name=row["name"],
                tier=row.get("tier"),
                starts=row.get("starts"),
                wins=row.get("wins"),
                podiums=row.get("podiums"),
                avg_fis_points=row.get("avg_fis_points")
            )
            for row in results
        ]

    else:
        # Default: list all athletes with career stats
        query = """
            SELECT fis_code, name, starts, wins, podiums, avg_fis_points
            FROM athlete_aggregate.basic_athlete_info_career
            ORDER BY wins DESC NULLS LAST
            LIMIT %(limit)s OFFSET %(offset)s
        """
        params = {"limit": limit, "offset": offset}
        results = execute_query(query, params)

        athletes = [
            AthleteListItem(
                fis_code=row["fis_code"],
                name=row["name"],
                starts=row.get("starts"),
                wins=row.get("wins"),
                podiums=row.get("podiums"),
                avg_fis_points=row.get("avg_fis_points")
            )
            for row in results
        ]

    return AthleteListResponse(
        data=athletes,
        pagination=PaginationMeta(
            total=None,  # TODO: Count total for better pagination
            limit=limit,
            offset=offset,
            has_more=len(athletes) == limit
        )
    )


@router.get("/athletes/{fis_code}", response_model=AthleteProfile)
def get_athlete(
    fis_code: str = Path(..., description="FIS athlete code")
):
    """
    Get complete athlete profile.

    Returns career statistics, current tier, and momentum data.
    """
    logger.info(f"GET /athletes/{fis_code}")

    # Get career stats
    career_query = """
        SELECT fis_code, name, starts, wins, podiums, avg_fis_points
        FROM athlete_aggregate.basic_athlete_info_career
        WHERE fis_code = %(fis_code)s
    """
    career_row = execute_query_single(career_query, {"fis_code": fis_code})

    if not career_row:
        raise HTTPException(
            status_code=404,
            detail=f"Athlete with FIS code '{fis_code}' not found"
        )

    # Get current tier (most recent year)
    tier_query = """
        SELECT discipline, tier, year, avg_fis_points, race_count
        FROM athlete_aggregate.performance_tiers
        WHERE fis_code = %(fis_code)s
        ORDER BY year DESC
        LIMIT 1
    """
    tier_row = execute_query_single(tier_query, {"fis_code": fis_code})

    # Get current momentum (most recent race)
    momentum_query = """
        SELECT momentum_z, date
        FROM athlete_aggregate.hot_streak
        WHERE fis_code = %(fis_code)s
        ORDER BY date DESC
        LIMIT 1
    """
    momentum_row = execute_query_single(momentum_query, {"fis_code": fis_code})

    # Build response
    profile = AthleteProfile(
        fis_code=career_row["fis_code"],
        name=career_row["name"],
        country=None,  # Not in career stats table
        career_stats=AthleteCareerStats(
            starts=career_row["starts"],
            wins=career_row["wins"],
            podiums=career_row["podiums"],
            avg_fis_points=career_row["avg_fis_points"]
        )
    )

    if tier_row:
        profile.current_tier = AthleteTier(
            tier=tier_row["tier"],
            discipline=tier_row["discipline"],
            year=tier_row["year"],
            avg_fis_points=tier_row["avg_fis_points"],
            race_count=tier_row["race_count"]
        )

    if momentum_row and momentum_row.get("momentum_z") is not None:
        momentum_z = momentum_row["momentum_z"]
        trend = "hot" if momentum_z > 1 else "cold" if momentum_z < -1 else "neutral"

        profile.momentum = AthleteMomentum(
            current_momentum_z=momentum_z,
            trend=trend,
            last_updated=momentum_row["date"]
        )

    return profile


@router.get("/athletes/{fis_code}/races", response_model=AthleteRaceHistoryResponse)
def get_athlete_races(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Get athlete's race history.

    Returns list of races with performance metrics.
    """
    logger.info(f"GET /athletes/{fis_code}/races - discipline={discipline}")

    query = """
        SELECT
            hs.race_id,
            hs.date,
            rd.location,
            rd.country,
            hs.discipline,
            hs.rank,
            hs.fis_points,
            hs.race_z_score,
            hs.momentum_z
        FROM athlete_aggregate.hot_streak hs
        JOIN raw.race_details rd ON hs.race_id = rd.race_id
        WHERE hs.fis_code = %(fis_code)s
    """

    params = {"fis_code": fis_code, "limit": limit, "offset": offset}

    if discipline:
        query += " AND hs.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY hs.date DESC LIMIT %(limit)s OFFSET %(offset)s"

    results = execute_query(query, params)

    if not results and offset == 0:
        # Athlete exists but has no race history
        raise HTTPException(
            status_code=404,
            detail=f"No race history found for athlete '{fis_code}'"
        )

    races = [
        AthleteRaceHistoryItem(
            race_id=row["race_id"],
            date=row["date"],
            location=row["location"],
            country=row.get("country"),
            discipline=row["discipline"],
            rank=row.get("rank"),
            fis_points=row.get("fis_points"),
            race_z_score=row.get("race_z_score"),
            momentum_z=row.get("momentum_z")
        )
        for row in results
    ]

    return AthleteRaceHistoryResponse(
        data=races,
        pagination=PaginationMeta(
            total=None,
            limit=limit,
            offset=offset,
            has_more=len(races) == limit
        )
    )


@router.get("/athletes/{fis_code}/momentum", response_model=MomentumResponse)
def get_athlete_momentum(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Get athlete's momentum/hot streak data over time.

    Useful for time series charts.
    """
    logger.info(f"GET /athletes/{fis_code}/momentum - discipline={discipline}")

    query = """
        SELECT
            date,
            race_id,
            momentum_z,
            race_z_score,
            ewma_race_z
        FROM athlete_aggregate.hot_streak
        WHERE fis_code = %(fis_code)s
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY date ASC LIMIT %(limit)s"

    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No momentum data found for athlete '{fis_code}'"
        )

    datapoints = [
        MomentumDataPoint(
            date=row["date"],
            race_id=row["race_id"],
            momentum_z=row["momentum_z"],
            race_z_score=row["race_z_score"],
            ewma_race_z=row.get("ewma_race_z")
        )
        for row in results
    ]

    return MomentumResponse(data=datapoints)


@router.get("/athletes/{fis_code}/courses", response_model=CoursePerformanceResponse)
def get_athlete_courses(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    min_races: int = Query(3, ge=1, description="Minimum races at location"),
):
    """
    Get athlete's performance by course/location.

    Shows which courses the athlete performs best/worst at.
    """
    logger.info(f"GET /athletes/{fis_code}/courses - discipline={discipline}, min_races={min_races}")

    query = """
        SELECT
            location,
            discipline,
            race_count,
            mean_race_z_score,
            mean_points_gained
        FROM course_aggregate.location_performance
        WHERE fis_code = %(fis_code)s
        AND race_count >= %(min_races)s
    """

    params = {"fis_code": fis_code, "min_races": min_races}

    if discipline:
        query += " AND discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY mean_race_z_score DESC"

    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No course performance data found for athlete '{fis_code}' (min {min_races} races)"
        )

    courses = [
        CoursePerformanceItem(
            location=row["location"],
            discipline=row["discipline"],
            race_count=row["race_count"],
            mean_race_z_score=row["mean_race_z_score"],
            mean_points_gained=row.get("mean_points_gained")
        )
        for row in results
    ]

    return CoursePerformanceResponse(data=courses)


@router.get("/athletes/{fis_code}/strokes-gained", response_model=StrokesGainedResponse)
def get_athlete_strokes_gained(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Get athlete's strokes gained data.

    Shows how many strokes the athlete gained/lost compared to field average.
    """
    logger.info(f"GET /athletes/{fis_code}/strokes-gained - discipline={discipline}")

    query = """
        SELECT
            sg.race_id,
            rd.date,
            rd.location,
            rd.country,
            rd.discipline,
            fr.rank,
            sg.strokes_gained,
            NULL as strokes_gained_percentile
        FROM race_aggregate.strokes_gained sg
        JOIN raw.race_details rd ON sg.race_id = rd.race_id
        LEFT JOIN raw.fis_results fr ON sg.race_id = fr.race_id AND sg.fis_code = fr.fis_code
        WHERE sg.fis_code = %(fis_code)s
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND rd.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY rd.date DESC LIMIT %(limit)s"

    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No strokes gained data found for athlete '{fis_code}'"
        )

    data = [
        StrokesGainedItem(
            race_id=row["race_id"],
            date=row["date"],
            location=row["location"],
            country=row.get("country"),
            discipline=row["discipline"],
            rank=row.get("rank"),
            strokes_gained=row.get("strokes_gained"),
            strokes_gained_percentile=row.get("strokes_gained_percentile")
        )
        for row in results
    ]

    return StrokesGainedResponse(data=data)


@router.get("/athletes/{fis_code}/strokes-gained-bib", response_model=StrokesGainedBibResponse)
def get_athlete_strokes_gained_bib(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Get athlete's bib-relative performance.

    Shows if athlete performs better/worse than expected based on bib position.
    Positive strokes_gained_bib means performed better than expected.
    """
    logger.info(f"GET /athletes/{fis_code}/strokes-gained-bib - discipline={discipline}")

    query = """
        SELECT
            sgb.race_id,
            rd.date,
            rd.location,
            rd.discipline,
            sgb.bib,
            fr.rank,
            NULL as expected_rank,
            sgb.strokes_gained_bib as bib_advantage
        FROM race_aggregate.strokes_gained_bib_relative sgb
        JOIN raw.race_details rd ON sgb.race_id = rd.race_id
        LEFT JOIN raw.fis_results fr ON sgb.race_id = fr.race_id AND sgb.fis_code = fr.fis_code
        WHERE sgb.fis_code = %(fis_code)s
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND rd.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY rd.date DESC LIMIT %(limit)s"

    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No bib-relative performance data found for athlete '{fis_code}'"
        )

    data = [
        StrokesGainedBibItem(
            race_id=row["race_id"],
            date=row["date"],
            location=row["location"],
            discipline=row["discipline"],
            bib=row.get("bib"),
            rank=row.get("rank"),
            expected_rank=row.get("expected_rank"),
            bib_advantage=row.get("bib_advantage")
        )
        for row in results
    ]

    return StrokesGainedBibResponse(data=data)


@router.get("/athletes/{fis_code}/regression", response_model=RegressionResponse)
def get_athlete_regression(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    year: Optional[int] = Query(None, description="Filter by year"),
):
    """
    Get athlete's course regression analysis.

    Shows how course characteristics (vertical drop, gate count, altitude)
    correlate with performance.

    When year filter is provided, calculates live from raw race data.
    When no year filter, uses pre-computed aggregate for speed.
    """
    logger.info(f"GET /athletes/{fis_code}/regression - discipline={discipline}, year={year}")

    # If year filter provided, calculate live from raw data
    if year:
        query = """
            WITH race_data AS (
                SELECT
                    r.fis_code,
                    r.discipline,
                    r.race_z_score,
                    c.vertical_drop,
                    c.gate_count,
                    c.start_altitude,
                    c.winning_time,
                    c.dnf_rate,
                    c.course_length
                FROM raw.fis_results r
                JOIN raw.race_details rd ON r.race_id = rd.race_id
                JOIN raw.courses c ON rd.course_id = c.course_id
                WHERE r.fis_code = %(fis_code)s
                    AND EXTRACT(YEAR FROM r.date) = %(year)s
                    AND r.race_z_score IS NOT NULL
        """

        params = {"fis_code": fis_code, "year": year}

        if discipline:
            query += " AND r.discipline = %(discipline)s"
            params["discipline"] = discipline

        query += """
            ),
            trait_stats AS (
                SELECT
                    'vertical_drop' as trait,
                    CORR(vertical_drop, race_z_score) as coefficient,
                    REGR_R2(race_z_score, vertical_drop) as r_squared
                FROM race_data
                WHERE vertical_drop IS NOT NULL

                UNION ALL

                SELECT
                    'gate_count' as trait,
                    CORR(gate_count, race_z_score) as coefficient,
                    REGR_R2(race_z_score, gate_count) as r_squared
                FROM race_data
                WHERE gate_count IS NOT NULL

                UNION ALL

                SELECT
                    'start_altitude' as trait,
                    CORR(start_altitude, race_z_score) as coefficient,
                    REGR_R2(race_z_score, start_altitude) as r_squared
                FROM race_data
                WHERE start_altitude IS NOT NULL

                UNION ALL

                SELECT
                    'winning_time' as trait,
                    CORR(winning_time, race_z_score) as coefficient,
                    REGR_R2(race_z_score, winning_time) as r_squared
                FROM race_data
                WHERE winning_time IS NOT NULL

                UNION ALL

                SELECT
                    'dnf_rate' as trait,
                    CORR(dnf_rate, race_z_score) as coefficient,
                    REGR_R2(race_z_score, dnf_rate) as r_squared
                FROM race_data
                WHERE dnf_rate IS NOT NULL
            )
            SELECT
                %(fis_code)s as fis_code,
                %(discipline_out)s as discipline,
                trait as characteristic,
                coefficient,
                NULL as std_error,
                NULL as p_value,
                r_squared
            FROM trait_stats
            WHERE coefficient IS NOT NULL
        """

        params["discipline_out"] = discipline or "All"
        results = execute_query(query, params)
    else:
        # Use pre-computed aggregate table (fast path)
        query = """
            SELECT
                fis_code,
                discipline,
                trait as characteristic,
                coefficient,
                NULL as std_error,
                NULL as p_value,
                r_squared
            FROM athlete_aggregate.course_regression
            WHERE fis_code = %(fis_code)s
        """

        params = {"fis_code": fis_code}

        if discipline:
            query += " AND discipline = %(discipline)s"
            params["discipline"] = discipline

        results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No regression data found for athlete '{fis_code}'"
        )

    # Get discipline from first row
    fis_code_value = results[0]["fis_code"]
    discipline_value = results[0]["discipline"]

    data = [
        RegressionCoefficient(
            characteristic=row["characteristic"],
            coefficient=row.get("coefficient"),
            std_error=row.get("std_error"),
            p_value=row.get("p_value"),
            r_squared=row.get("r_squared")
        )
        for row in results
    ]

    return RegressionResponse(
        fis_code=fis_code_value,
        discipline=discipline_value,
        data=data
    )


@router.get("/athletes/{fis_code}/course-traits", response_model=CourseTraitResponse)
def get_athlete_course_traits(
    fis_code: str = Path(..., description="FIS athlete code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    year: Optional[int] = Query(None, description="Filter by year"),
):
    """
    Get athlete's performance by course trait bins.

    Shows performance in different bins for course characteristics
    like vertical drop, gate count, and altitude.

    When year filter is provided, calculates live from raw race data.
    When no year filter, uses pre-computed aggregate for speed.
    """
    logger.info(f"GET /athletes/{fis_code}/course-traits - discipline={discipline}, year={year}")

    # If year filter provided, calculate live from raw data
    if year:
        query = """
            WITH race_data AS (
                SELECT
                    r.fis_code,
                    r.discipline,
                    r.race_z_score,
                    c.vertical_drop,
                    c.gate_count,
                    c.start_altitude
                FROM raw.fis_results r
                JOIN raw.race_details rd ON r.race_id = rd.race_id
                JOIN raw.courses c ON rd.course_id = c.course_id
                WHERE r.fis_code = %(fis_code)s
                    AND EXTRACT(YEAR FROM r.date) = %(year)s
                    AND r.race_z_score IS NOT NULL
        """

        params = {"fis_code": fis_code, "year": year}

        if discipline:
            query += " AND r.discipline = %(discipline)s"
            params["discipline"] = discipline

        query += """
            ),
            trait_quintiles AS (
                -- Vertical Drop quintiles
                SELECT
                    'vertical_drop' as trait,
                    CASE
                        WHEN vertical_drop IS NULL THEN NULL
                        WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 0
                        WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 1
                        WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 2
                        WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 3
                        ELSE 4
                    END as quintile,
                    race_z_score
                FROM race_data
                WHERE vertical_drop IS NOT NULL

                UNION ALL

                -- Gate Count quintiles
                SELECT
                    'gate_count' as trait,
                    CASE
                        WHEN gate_count IS NULL THEN NULL
                        WHEN gate_count <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 0
                        WHEN gate_count <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 1
                        WHEN gate_count <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 2
                        WHEN gate_count <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 3
                        ELSE 4
                    END as quintile,
                    race_z_score
                FROM race_data
                WHERE gate_count IS NOT NULL

                UNION ALL

                -- Start Altitude quintiles
                SELECT
                    'start_altitude' as trait,
                    CASE
                        WHEN start_altitude IS NULL THEN NULL
                        WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 0
                        WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 1
                        WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 2
                        WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 3
                        ELSE 4
                    END as quintile,
                    race_z_score
                FROM race_data
                WHERE start_altitude IS NOT NULL
            )
            SELECT
                %(fis_code)s as fis_code,
                %(discipline_out)s as discipline,
                trait,
                quintile,
                'Q' || (quintile + 1) as quintile_label,
                COUNT(*) as race_count,
                AVG(race_z_score) as avg_z_score,
                NULL as avg_rank
            FROM trait_quintiles
            WHERE quintile IS NOT NULL
            GROUP BY trait, quintile
            ORDER BY trait, quintile
        """

        params["discipline_out"] = discipline or "All"
        results = execute_query(query, params)
    else:
        # Use pre-computed aggregate table (fast path)
        query = """
            SELECT
                fis_code,
                discipline,
                trait,
                CAST(SUBSTRING(trait_bin FROM '[0-9]+') AS INTEGER) as quintile,
                trait_bin as quintile_label,
                race_count,
                avg_z_score,
                NULL as avg_rank
            FROM athlete_aggregate.course_traits
            WHERE fis_code = %(fis_code)s
        """

        params = {"fis_code": fis_code}

        if discipline:
            query += " AND discipline = %(discipline)s"
            params["discipline"] = discipline

        query += " ORDER BY trait, trait_bin"
        results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No course trait data found for athlete '{fis_code}'"
        )

    # Get fis_code and discipline from first row
    fis_code_value = results[0]["fis_code"]
    discipline_value = results[0].get("discipline")

    data = [
        CourseTraitQuintileItem(
            trait=row["trait"],
            quintile=row.get("quintile") or 0,
            quintile_label=row["quintile_label"],
            race_count=row["race_count"],
            avg_z_score=row.get("avg_z_score"),
            avg_rank=row.get("avg_rank")
        )
        for row in results
    ]

    return CourseTraitResponse(
        fis_code=fis_code_value,
        discipline=discipline_value,
        data=data
    )
