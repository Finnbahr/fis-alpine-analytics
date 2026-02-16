-- Export recent data (last 3 years) for demo deployment
-- This will create a much smaller database suitable for free tier

-- First, create a temporary table with FIS codes of active athletes (from recent races)
CREATE TEMP TABLE IF NOT EXISTS recent_athletes AS
SELECT DISTINCT fisCode
FROM raw.fis_results
WHERE raceDate >= '2023-01-01'
LIMIT 5000;

-- Export raw race results (last 3 years only)
COPY (
  SELECT * FROM raw.fis_results
  WHERE raceDate >= '2023-01-01'
  ORDER BY raceDate DESC
  LIMIT 100000
) TO '/tmp/fis_results_recent.csv' CSV HEADER;

-- Export race details for those results
COPY (
  SELECT DISTINCT rd.* FROM raw.race_details rd
  INNER JOIN raw.fis_results fr ON rd.raceId = fr.raceId
  WHERE fr.raceDate >= '2023-01-01'
) TO '/tmp/race_details_recent.csv' CSV HEADER;

-- Export athlete aggregates for active athletes
COPY (
  SELECT * FROM athlete_aggregate.athlete_career_stats
  WHERE fis_code IN (SELECT fisCode FROM recent_athletes)
) TO '/tmp/athlete_career_stats.csv' CSV HEADER;

COPY (
  SELECT * FROM athlete_aggregate.athlete_discipline_stats
  WHERE fis_code IN (SELECT fisCode FROM recent_athletes)
) TO '/tmp/athlete_discipline_stats.csv' CSV HEADER;

COPY (
  SELECT * FROM athlete_aggregate.athlete_momentum
  WHERE fis_code IN (SELECT fisCode FROM recent_athletes)
  AND date >= '2023-01-01'
) TO '/tmp/athlete_momentum.csv' CSV HEADER;

-- Export course aggregates
COPY (
  SELECT * FROM course_aggregate.course_difficulty
  WHERE discipline IS NOT NULL
  LIMIT 500
) TO '/tmp/course_difficulty.csv' CSV HEADER;

-- Export race aggregates for recent races
COPY (
  SELECT ra.* FROM race_aggregate.race_summary ra
  INNER JOIN raw.fis_results fr ON ra.race_id = fr.raceId
  WHERE fr.raceDate >= '2023-01-01'
  GROUP BY ra.race_id
  LIMIT 10000
) TO '/tmp/race_summary.csv' CSV HEADER;
