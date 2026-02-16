"""
Pydantic models for request/response validation.
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import date


# ========== Common Models ==========

class PaginationMeta(BaseModel):
    """Pagination metadata."""
    total: Optional[int] = None
    limit: int
    offset: int
    has_more: bool


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    pagination: PaginationMeta


# ========== Athlete Models ==========

class AthleteBasic(BaseModel):
    """Basic athlete information."""
    fis_code: str
    name: str
    country: Optional[str] = None


class AthleteCareerStats(BaseModel):
    """Athlete career statistics."""
    starts: int
    wins: int
    podiums: int
    avg_fis_points: float


class AthleteTier(BaseModel):
    """Athlete performance tier."""
    tier: str
    discipline: str
    year: int
    avg_fis_points: float
    race_count: int


class AthleteMomentum(BaseModel):
    """Athlete current momentum."""
    current_momentum_z: Optional[float] = None
    trend: Optional[str] = None  # "hot", "cold", "neutral"
    last_updated: Optional[date] = None


class AthleteProfile(AthleteBasic):
    """Complete athlete profile."""
    career_stats: Optional[AthleteCareerStats] = None
    current_tier: Optional[AthleteTier] = None
    momentum: Optional[AthleteMomentum] = None


class AthleteListItem(BaseModel):
    """Athlete in list view."""
    fis_code: str
    name: str
    country: Optional[str] = None
    tier: Optional[str] = None
    starts: Optional[int] = None
    wins: Optional[int] = None
    podiums: Optional[int] = None
    avg_fis_points: Optional[float] = None


class AthleteListResponse(PaginatedResponse):
    """Paginated list of athletes."""
    data: List[AthleteListItem]


# ========== Race Models ==========

class RaceBasic(BaseModel):
    """Basic race information."""
    race_id: int
    date: date
    location: str
    country: Optional[str] = None
    discipline: str
    race_type: Optional[str] = None


class RaceDetails(RaceBasic):
    """Detailed race information."""
    vertical_drop: Optional[float] = None
    start_altitude: Optional[float] = None
    gate_count: Optional[int] = None
    competitor_count: Optional[int] = None


class RaceResult(BaseModel):
    """Individual race result."""
    rank: str
    fis_code: str
    name: str
    country: Optional[str] = None
    bib: Optional[int] = None
    time: Optional[str] = None
    fis_points: Optional[float] = None
    race_z_score: Optional[float] = None


class RaceResultsResponse(BaseModel):
    """Race results with details."""
    race: RaceBasic
    results: List[RaceResult]


class RaceListResponse(PaginatedResponse):
    """Paginated list of races."""
    data: List[RaceBasic]


# ========== Athlete Race History Models ==========

class AthleteRaceHistoryItem(BaseModel):
    """Single race in athlete's history."""
    race_id: int
    date: date
    location: str
    country: Optional[str] = None
    discipline: str
    rank: Optional[str] = None
    fis_points: Optional[float] = None
    race_z_score: Optional[float] = None
    momentum_z: Optional[float] = None


class AthleteRaceHistoryResponse(PaginatedResponse):
    """Athlete's race history."""
    data: List[AthleteRaceHistoryItem]


# ========== Momentum/Hot Streak Models ==========

class MomentumDataPoint(BaseModel):
    """Single momentum data point."""
    date: date
    race_id: int
    momentum_z: float
    race_z_score: float
    ewma_race_z: Optional[float] = None


class MomentumResponse(BaseModel):
    """Momentum time series."""
    data: List[MomentumDataPoint]


# ========== Course Performance Models ==========

class CoursePerformanceItem(BaseModel):
    """Athlete performance at specific course."""
    location: str
    discipline: str
    race_count: int
    mean_race_z_score: float
    mean_points_gained: Optional[float] = None


class CoursePerformanceResponse(BaseModel):
    """Athlete's course performance."""
    data: List[CoursePerformanceItem]


# ========== Search Models ==========

class SearchResultAthlete(BaseModel):
    """Athlete search result."""
    type: str = "athlete"
    fis_code: str
    name: str
    country: Optional[str] = None
    starts: Optional[int] = None
    wins: Optional[int] = None


class SearchResultLocation(BaseModel):
    """Location search result."""
    type: str = "location"
    location: str
    country: Optional[str] = None
    race_count: Optional[int] = None


class SearchResults(BaseModel):
    """Search results container."""
    athletes: List[SearchResultAthlete] = []
    locations: List[SearchResultLocation] = []


class SearchResponse(BaseModel):
    """Global search response."""
    query: str
    results: SearchResults
    total_results: int


# ========== Leaderboard Models ==========

class LeaderboardAthleteItem(BaseModel):
    """Athlete in leaderboard."""
    rank: int
    fis_code: str
    name: str
    country: Optional[str] = None
    avg_fis_points: float
    race_count: int
    wins: Optional[int] = None
    podiums: Optional[int] = None


class LeaderboardResponse(BaseModel):
    """Leaderboard response."""
    discipline: str
    tier: Optional[str] = None
    year: Optional[int] = None
    data: List[LeaderboardAthleteItem]
    pagination: Optional[PaginationMeta] = None


# ========== Hot Streak Models ==========

class HotStreakAthleteItem(BaseModel):
    """Athlete in hot streak leaderboard."""
    rank: int
    fis_code: str
    name: str
    country: Optional[str] = None
    discipline: str
    momentum_z: float
    recent_races: int
    last_race_date: date


class HotStreakResponse(BaseModel):
    """Hot streak leaderboard response."""
    discipline: Optional[str] = None
    days: int
    data: List[HotStreakAthleteItem]


# ========== Course Models ==========

class CourseBasic(BaseModel):
    """Basic course information."""
    location: str
    country: Optional[str] = None
    discipline: str
    race_count: int


class CourseDifficulty(BaseModel):
    """Course difficulty metrics."""
    location: str
    discipline: str
    homologation_number: Optional[str] = None
    hill_difficulty_index: float
    avg_dnf_rate: float
    race_count: int
    avg_winning_time: Optional[str] = None
    avg_gate_count: Optional[float] = None
    avg_start_altitude: Optional[float] = None
    avg_vertical_drop: Optional[float] = None


class CourseListResponse(PaginatedResponse):
    """Paginated list of courses."""
    data: List[CourseBasic]


class CourseDifficultyResponse(BaseModel):
    """Course difficulty rankings."""
    discipline: str
    data: List[CourseDifficulty]


# ========== Analytics Models ==========

class HomeAdvantageItem(BaseModel):
    """Home advantage statistics for a country."""
    country: str
    discipline: str
    sex: Optional[str] = None
    home_race_count: int
    away_race_count: int
    home_avg_fis_points: float
    away_avg_fis_points: float
    fis_points_pct_diff: float  # Negative = home advantage


class HomeAdvantageResponse(BaseModel):
    """Home advantage analysis."""
    discipline: Optional[str] = None
    data: List[HomeAdvantageItem]


# ========== Strokes Gained Models ==========

class StrokesGainedItem(BaseModel):
    """Single strokes gained data point."""
    race_id: int
    date: date
    location: str
    country: Optional[str] = None
    discipline: str
    rank: Optional[str] = None
    strokes_gained: Optional[float] = None
    strokes_gained_percentile: Optional[float] = None


class StrokesGainedResponse(BaseModel):
    """Athlete's strokes gained history."""
    data: List[StrokesGainedItem]


class StrokesGainedBibItem(BaseModel):
    """Bib-relative performance data."""
    race_id: int
    date: date
    location: str
    discipline: str
    bib: Optional[int] = None
    rank: Optional[str] = None
    expected_rank: Optional[float] = None
    bib_advantage: Optional[float] = None  # Negative = performed better than expected


class StrokesGainedBibResponse(BaseModel):
    """Athlete's bib-relative performance."""
    data: List[StrokesGainedBibItem]


# ========== Regression Analysis Models ==========

class RegressionCoefficient(BaseModel):
    """Regression coefficient for a course characteristic."""
    characteristic: str  # e.g., "vertical_drop", "gate_count", "altitude"
    coefficient: Optional[float] = None
    std_error: Optional[float] = None
    p_value: Optional[float] = None
    r_squared: Optional[float] = None


class RegressionResponse(BaseModel):
    """Athlete's course regression analysis."""
    fis_code: str
    discipline: str
    data: List[RegressionCoefficient]


# ========== Course Traits Models ==========

class CourseTraitQuintileItem(BaseModel):
    """Performance in a course trait quintile."""
    trait: str  # e.g., "vertical_drop", "gate_count", "altitude"
    quintile: int  # 1-5
    quintile_label: str  # e.g., "Very Low", "Low", "Medium", "High", "Very High"
    race_count: int
    avg_z_score: Optional[float] = None
    avg_rank: Optional[float] = None


class CourseTraitResponse(BaseModel):
    """Athlete's performance by course traits."""
    fis_code: str
    discipline: Optional[str] = None
    data: List[CourseTraitQuintileItem]


# ========== Error Models ==========

class ErrorDetail(BaseModel):
    """Error detail."""
    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    error: ErrorDetail
