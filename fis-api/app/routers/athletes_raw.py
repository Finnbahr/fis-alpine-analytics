"""
Athletes API endpoints - Raw Data Version
Calculates all metrics from raw.fis_results without needing aggregate tables.
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
    name: Optional[str] = Query(None, description="Search by athlete name"),
    country: Optional[str] = Query(None, description="Filter by country code"),
    discipline: Optional[str] = Query(None, description="Filter by discipline"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List or search athletes - calculated from raw data."""
    logger.info(f"GET /athletes - name={name}, country={country}, discipline={discipline}")

    # Build query from raw.fis_results
    query = """
        SELECT
            fis_code,
            name,
            country,
            COUNT(*) as starts,
            COUNT(CASE WHEN rank = '1' THEN 1 END) as wins,
            COUNT(CASE WHEN rank ~ '^[0-9]+$' AND rank::int <= 3 THEN 1 END) as podiums,
            ROUND(AVG(fis_points)::numeric, 2) as avg_fis_points
        FROM raw.fis_results
        WHERE fis_code IS NOT NULL AND name IS NOT NULL
    """

    params = {"limit": limit, "offset": offset}

    if name:
        query += " AND LOWER(name) LIKE LOWER(%(name)s)"
        params["name"] = f"%{name}%"

    if country:
        query += " AND country = %(country)s"
        params["country"] = country

    if discipline:
        query += " AND discipline = %(discipline)s"
        params["discipline"] = discipline

    query += """
        GROUP BY fis_code, name, country
        ORDER BY COUNT(CASE WHEN rank = '1' THEN 1 END) DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """

    results = execute_query(query, params)

    athletes = [
        AthleteListItem(
            fis_code=row["fis_code"],
            name=row["name"],
            country=row.get("country"),
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
            total=None,
            limit=limit,
            offset=offset,
            has_more=len(athletes) == limit
        )
    )


@router.get("/athletes/{fis_code}", response_model=AthleteProfile)
def get_athlete(fis_code: str = Path(..., description="FIS athlete code")):
    """Get athlete profile - calculated from raw data."""
    logger.info(f"GET /athletes/{fis_code}")

    # Get career stats from raw data
    career_query = """
        SELECT
            fis_code,
            name,
            country,
            COUNT(*) as starts,
            COUNT(CASE WHEN rank = '1' THEN 1 END) as wins,
            COUNT(CASE WHEN rank ~ '^[0-9]+$' AND rank::int <= 3 THEN 1 END) as podiums,
            ROUND(AVG(fis_points)::numeric, 2) as avg_fis_points
        FROM raw.fis_results
        WHERE fis_code = %(fis_code)s
        GROUP BY fis_code, name, country
    """

    career_row = execute_query_single(career_query, {"fis_code": fis_code})

    if not career_row:
        raise HTTPException(
            status_code=404,
            detail=f"Athlete with FIS code '{fis_code}' not found"
        )

    # Build profile
    profile = AthleteProfile(
        fis_code=career_row["fis_code"],
        name=career_row["name"],
        country=career_row.get("country"),
        career_stats=AthleteCareerStats(
            starts=career_row["starts"],
            wins=career_row["wins"],
            podiums=career_row["podiums"],
            avg_fis_points=career_row["avg_fis_points"]
        )
    )

    return profile


@router.get("/athletes/{fis_code}/races", response_model=AthleteRaceHistoryResponse)
def get_athlete_races(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get athlete race history - calculated from raw data with z-scores."""
    logger.info(f"GET /athletes/{fis_code}/races")

    query = """
        WITH race_stats AS (
            SELECT
                race_id,
                AVG(fis_points) as mean_points,
                STDDEV(fis_points) as std_points
            FROM raw.fis_results
            WHERE fis_points IS NOT NULL
            GROUP BY race_id
        )
        SELECT
            r.race_id,
            r.date,
            rd.location,
            rd.country,
            r.discipline,
            r.rank,
            r.fis_points,
            CASE
                WHEN rs.std_points > 0 AND rs.mean_points IS NOT NULL
                THEN (rs.mean_points - r.fis_points) / rs.std_points
                ELSE NULL
            END as race_z_score
        FROM raw.fis_results r
        LEFT JOIN raw.race_details rd ON r.race_id = rd.race_id
        LEFT JOIN race_stats rs ON r.race_id = rs.race_id
        WHERE r.fis_code = %(fis_code)s
    """

    params = {"fis_code": fis_code, "limit": limit, "offset": offset}

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY r.date DESC LIMIT %(limit)s OFFSET %(offset)s"

    results = execute_query(query, params)

    if not results and offset == 0:
        raise HTTPException(
            status_code=404,
            detail=f"No race history found for athlete '{fis_code}'"
        )

    races = [
        AthleteRaceHistoryItem(
            race_id=row["race_id"],
            date=row["date"],
            location=row.get("location"),
            country=row.get("country"),
            discipline=row["discipline"],
            rank=row.get("rank"),
            fis_points=row.get("fis_points"),
            race_z_score=row.get("race_z_score"),
            momentum_z=None  # Could calculate EWMA if needed
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
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Get athlete momentum - calculated from raw data."""
    logger.info(f"GET /athletes/{fis_code}/momentum")

    query = """
        WITH race_stats AS (
            SELECT
                race_id,
                AVG(fis_points) as mean_points,
                STDDEV(fis_points) as std_points
            FROM raw.fis_results
            WHERE fis_points IS NOT NULL
            GROUP BY race_id
        )
        SELECT
            r.date,
            r.race_id,
            CASE
                WHEN rs.std_points > 0 AND rs.mean_points IS NOT NULL
                THEN (rs.mean_points - r.fis_points) / rs.std_points
                ELSE NULL
            END as race_z_score
        FROM raw.fis_results r
        LEFT JOIN race_stats rs ON r.race_id = rs.race_id
        WHERE r.fis_code = %(fis_code)s
          AND r.fis_points IS NOT NULL
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY r.date ASC LIMIT %(limit)s"

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
            momentum_z=row["race_z_score"],  # Simplified - using z-score as momentum
            race_z_score=row["race_z_score"],
            ewma_race_z=None
        )
        for row in results
    ]

    return MomentumResponse(data=datapoints)


@router.get("/athletes/{fis_code}/courses", response_model=CoursePerformanceResponse)
def get_athlete_courses(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    min_races: int = Query(3, ge=1),
):
    """Get athlete course performance - calculated from raw data."""
    logger.info(f"GET /athletes/{fis_code}/courses")

    query = """
        WITH race_stats AS (
            SELECT
                race_id,
                AVG(fis_points) as mean_points,
                STDDEV(fis_points) as std_points
            FROM raw.fis_results
            WHERE fis_points IS NOT NULL
            GROUP BY race_id
        ),
        athlete_races AS (
            SELECT
                rd.location,
                r.discipline,
                r.fis_points,
                CASE
                    WHEN rs.std_points > 0 AND rs.mean_points IS NOT NULL
                    THEN (rs.mean_points - r.fis_points) / rs.std_points
                    ELSE NULL
                END as race_z_score
            FROM raw.fis_results r
            JOIN raw.race_details rd ON r.race_id = rd.race_id
            LEFT JOIN race_stats rs ON r.race_id = rs.race_id
            WHERE r.fis_code = %(fis_code)s
              AND r.fis_points IS NOT NULL
    """

    params = {"fis_code": fis_code, "min_races": min_races}

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += """
        )
        SELECT
            location,
            discipline,
            COUNT(*) as race_count,
            ROUND(AVG(race_z_score)::numeric, 3) as mean_race_z_score
        FROM athlete_races
        WHERE location IS NOT NULL
          AND race_z_score IS NOT NULL
        GROUP BY location, discipline
        HAVING COUNT(*) >= %(min_races)s
        ORDER BY AVG(race_z_score) DESC
        LIMIT 20
    """

    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No course performance data found for athlete '{fis_code}'"
        )

    courses = [
        CoursePerformanceItem(
            location=row["location"],
            discipline=row["discipline"],
            race_count=row["race_count"],
            mean_race_z_score=row["mean_race_z_score"],
            mean_points_gained=None
        )
        for row in results
    ]

    return CoursePerformanceResponse(data=courses)


@router.get("/athletes/{fis_code}/strokes-gained", response_model=StrokesGainedResponse)
def get_athlete_strokes_gained(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Get strokes gained - calculated from raw data."""
    logger.info(f"GET /athletes/{fis_code}/strokes-gained")

    query = """
        WITH race_stats AS (
            SELECT
                race_id,
                AVG(fis_points) as mean_points,
                STDDEV(fis_points) as std_points
            FROM raw.fis_results
            WHERE fis_points IS NOT NULL
            GROUP BY race_id
        )
        SELECT
            r.race_id,
            r.date,
            rd.location,
            rd.country,
            r.discipline,
            r.rank,
            CASE
                WHEN rs.mean_points IS NOT NULL
                THEN rs.mean_points - r.fis_points
                ELSE NULL
            END as strokes_gained
        FROM raw.fis_results r
        LEFT JOIN raw.race_details rd ON r.race_id = rd.race_id
        LEFT JOIN race_stats rs ON r.race_id = rs.race_id
        WHERE r.fis_code = %(fis_code)s
          AND r.fis_points IS NOT NULL
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY r.date DESC LIMIT %(limit)s"

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
            location=row.get("location"),
            country=row.get("country"),
            discipline=row["discipline"],
            rank=row.get("rank"),
            strokes_gained=row.get("strokes_gained"),
            strokes_gained_percentile=None
        )
        for row in results
    ]

    return StrokesGainedResponse(data=data)


@router.get("/athletes/{fis_code}/strokes-gained-bib", response_model=StrokesGainedBibResponse)
def get_athlete_strokes_gained_bib(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
):
    """Get bib advantage - calculated from raw data."""
    logger.info(f"GET /athletes/{fis_code}/strokes-gained-bib")

    query = """
        WITH race_bib_stats AS (
            SELECT
                race_id,
                CORR(bib::numeric, rank::numeric) as bib_rank_corr
            FROM raw.fis_results
            WHERE bib IS NOT NULL
              AND rank IS NOT NULL
              AND rank ~ '^[0-9]+$'
            GROUP BY race_id
            HAVING COUNT(*) >= 20
        )
        SELECT
            r.race_id,
            r.date,
            rd.location,
            r.discipline,
            r.bib,
            r.rank,
            CASE
                WHEN rbs.bib_rank_corr > 0.3
                THEN (30 - r.bib::numeric) * rbs.bib_rank_corr
                ELSE 0
            END as bib_advantage
        FROM raw.fis_results r
        LEFT JOIN raw.race_details rd ON r.race_id = rd.race_id
        LEFT JOIN race_bib_stats rbs ON r.race_id = rbs.race_id
        WHERE r.fis_code = %(fis_code)s
          AND r.bib IS NOT NULL
          AND r.rank IS NOT NULL
    """

    params = {"fis_code": fis_code, "limit": limit}

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += " ORDER BY r.date DESC LIMIT %(limit)s"

    results = execute_query(query, params)

    if not results:
        # Return empty data instead of 404 - bib data is optional
        return StrokesGainedBibResponse(data=[])

    data = [
        StrokesGainedBibItem(
            race_id=row["race_id"],
            date=row["date"],
            location=row.get("location"),
            discipline=row["discipline"],
            bib=row.get("bib"),
            rank=row.get("rank"),
            expected_rank=None,
            bib_advantage=row.get("bib_advantage")
        )
        for row in results
    ]

    return StrokesGainedBibResponse(data=data)


# Keep the existing regression and course traits endpoints as they already do live calculation
@router.get("/athletes/{fis_code}/regression", response_model=RegressionResponse)
def get_athlete_regression(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
):
    """Course regression analysis - always calculated live from raw data."""
    logger.info(f"GET /athletes/{fis_code}/regression")

    query = """
        WITH race_data AS (
            SELECT
                r.fis_code,
                r.discipline,
                CASE
                    WHEN rs.std_points > 0 AND rs.mean_points IS NOT NULL
                    THEN (rs.mean_points - r.fis_points) / rs.std_points
                    ELSE NULL
                END as race_z_score,
                c.vertical_drop,
                c.gate_count,
                c.start_altitude
            FROM raw.fis_results r
            JOIN raw.race_details rd ON r.race_id = rd.race_id
            JOIN raw.courses c ON rd.course_id = c.course_id
            LEFT JOIN (
                SELECT race_id, AVG(fis_points) as mean_points, STDDEV(fis_points) as std_points
                FROM raw.fis_results WHERE fis_points IS NOT NULL GROUP BY race_id
            ) rs ON r.race_id = rs.race_id
            WHERE r.fis_code = %(fis_code)s
              AND r.fis_points IS NOT NULL
    """

    params = {"fis_code": fis_code}

    if year:
        query += " AND EXTRACT(YEAR FROM r.date) = %(year)s"
        params["year"] = year

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += """
        ),
        trait_stats AS (
            SELECT 'vertical_drop' as trait,
                   CORR(vertical_drop, race_z_score) as coefficient,
                   REGR_R2(race_z_score, vertical_drop) as r_squared
            FROM race_data WHERE vertical_drop IS NOT NULL

            UNION ALL

            SELECT 'gate_count',
                   CORR(gate_count, race_z_score),
                   REGR_R2(race_z_score, gate_count)
            FROM race_data WHERE gate_count IS NOT NULL

            UNION ALL

            SELECT 'start_altitude',
                   CORR(start_altitude, race_z_score),
                   REGR_R2(race_z_score, start_altitude)
            FROM race_data WHERE start_altitude IS NOT NULL
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

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No regression data found for athlete '{fis_code}'"
        )

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
        fis_code=fis_code,
        discipline=discipline or "All",
        data=data
    )


@router.get("/athletes/{fis_code}/course-traits", response_model=CourseTraitResponse)
def get_athlete_course_traits(
    fis_code: str = Path(...),
    discipline: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
):
    """Course trait analysis - always calculated live from raw data."""
    logger.info(f"GET /athletes/{fis_code}/course-traits")

    query = """
        WITH race_data AS (
            SELECT
                r.fis_code,
                r.discipline,
                CASE
                    WHEN rs.std_points > 0 AND rs.mean_points IS NOT NULL
                    THEN (rs.mean_points - r.fis_points) / rs.std_points
                    ELSE NULL
                END as race_z_score,
                c.vertical_drop,
                c.gate_count,
                c.start_altitude
            FROM raw.fis_results r
            JOIN raw.race_details rd ON r.race_id = rd.race_id
            JOIN raw.courses c ON rd.course_id = c.course_id
            LEFT JOIN (
                SELECT race_id, AVG(fis_points) as mean_points, STDDEV(fis_points) as std_points
                FROM raw.fis_results WHERE fis_points IS NOT NULL GROUP BY race_id
            ) rs ON r.race_id = rs.race_id
            WHERE r.fis_code = %(fis_code)s
              AND r.fis_points IS NOT NULL
    """

    params = {"fis_code": fis_code}

    if year:
        query += " AND EXTRACT(YEAR FROM r.date) = %(year)s"
        params["year"] = year

    if discipline:
        query += " AND r.discipline = %(discipline)s"
        params["discipline"] = discipline

    query += """
        ),
        trait_quintiles AS (
            SELECT 'vertical_drop' as trait,
                   CASE
                       WHEN vertical_drop IS NULL THEN NULL
                       WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 0
                       WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 1
                       WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 2
                       WHEN vertical_drop <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY vertical_drop) FROM race_data WHERE vertical_drop IS NOT NULL) THEN 3
                       ELSE 4
                   END as quintile,
                   race_z_score
            FROM race_data WHERE vertical_drop IS NOT NULL

            UNION ALL

            SELECT 'gate_count',
                   CASE
                       WHEN gate_count IS NULL THEN NULL
                       WHEN gate_count <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 0
                       WHEN gate_count <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 1
                       WHEN gate_count <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 2
                       WHEN gate_count <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY gate_count) FROM race_data WHERE gate_count IS NOT NULL) THEN 3
                       ELSE 4
                   END,
                   race_z_score
            FROM race_data WHERE gate_count IS NOT NULL

            UNION ALL

            SELECT 'start_altitude',
                   CASE
                       WHEN start_altitude IS NULL THEN NULL
                       WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.2) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 0
                       WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.4) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 1
                       WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.6) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 2
                       WHEN start_altitude <= (SELECT PERCENTILE_CONT(0.8) WITHIN GROUP (ORDER BY start_altitude) FROM race_data WHERE start_altitude IS NOT NULL) THEN 3
                       ELSE 4
                   END,
                   race_z_score
            FROM race_data WHERE start_altitude IS NOT NULL
        )
        SELECT
            %(fis_code)s as fis_code,
            %(discipline_out)s as discipline,
            trait,
            quintile,
            'Q' || (quintile + 1) as quintile_label,
            COUNT(*) as race_count,
            AVG(race_z_score) as avg_z_score
        FROM trait_quintiles
        WHERE quintile IS NOT NULL
        GROUP BY trait, quintile
        ORDER BY trait, quintile
    """

    params["discipline_out"] = discipline or "All"
    results = execute_query(query, params)

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No course trait data found for athlete '{fis_code}'"
        )

    data = [
        CourseTraitQuintileItem(
            trait=row["trait"],
            quintile=row["quintile"],
            quintile_label=row["quintile_label"],
            race_count=row["race_count"],
            avg_z_score=row.get("avg_z_score"),
            avg_rank=None
        )
        for row in results
    ]

    return CourseTraitResponse(
        fis_code=fis_code,
        discipline=discipline or "All",
        data=data
    )
