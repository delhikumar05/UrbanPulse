-- ============================================================
-- URBAN PULSE - 15 CORE SQL ANALYST INVESTIGATIONS
-- Database: urban_pulse_db
-- Main table: service_requests
-- Target: MySQL 8.0+ (recommended MySQL 8.4 LTS)
--
-- Run each numbered query separately in MySQL Workbench.
-- All queries use the cleaned, published service_requests table.
-- NULL and 'Unspecified' categories are retained unless a query
-- explicitly states otherwise.
-- ============================================================

USE urban_pulse_db;

-- ------------------------------------------------------------
-- 01. Total distinct request volume
-- ------------------------------------------------------------
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT unique_key) AS distinct_requests,
    COUNT(*) - COUNT(DISTINCT unique_key) AS duplicate_excess_rows
FROM service_requests;

-- ------------------------------------------------------------
-- 02. Top 10 complaint categories by request count
-- ------------------------------------------------------------
SELECT
    COALESCE(complaint_type, 'Missing complaint type') AS complaint_type,
    COUNT(*) AS request_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM service_requests), 2)
        AS percentage_of_sample
FROM service_requests
GROUP BY complaint_type
ORDER BY request_count DESC
LIMIT 10;

-- ------------------------------------------------------------
-- 03. Agency workload distribution
-- ------------------------------------------------------------
SELECT
    COALESCE(agency, 'Missing agency') AS agency,
    COALESCE(agency_name, 'Missing agency name') AS agency_name,
    COUNT(*) AS request_count,
    SUM(is_closed = TRUE) AS recorded_closed_count,
    SUM(is_closed = FALSE) AS recorded_open_count
FROM service_requests
GROUP BY agency, agency_name
ORDER BY request_count DESC;

-- ------------------------------------------------------------
-- 04. Request volume by borough
-- Keep NULL and 'Unspecified' visible as separate groups.
-- ------------------------------------------------------------
SELECT
    CASE
        WHEN borough IS NULL THEN 'NULL / Missing'
        ELSE borough
    END AS borough_group,
    COUNT(*) AS request_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM service_requests), 2)
        AS percentage_of_sample
FROM service_requests
GROUP BY borough
ORDER BY request_count DESC;

-- ------------------------------------------------------------
-- 05. Monthly request trend
-- This is the monthly distribution of the 100,000-record sample.
-- For full source monthly volume use the separate
-- nyc311_monthly_source_counts_*.csv report.
-- ------------------------------------------------------------
SELECT
    DATE_FORMAT(created_date, '%Y-%m') AS created_month,
    COUNT(*) AS sample_request_count
FROM service_requests
WHERE created_date IS NOT NULL
GROUP BY DATE_FORMAT(created_date, '%Y-%m')
ORDER BY created_month;

-- ------------------------------------------------------------
-- 06A. Demand by weekday
-- ------------------------------------------------------------
SELECT
    created_weekday AS weekday_name,
    COUNT(*) AS request_count
FROM service_requests
WHERE created_weekday IS NOT NULL
GROUP BY created_weekday
ORDER BY FIELD(
    created_weekday,
    'Monday', 'Tuesday', 'Wednesday', 'Thursday',
    'Friday', 'Saturday', 'Sunday'
);

-- ------------------------------------------------------------
-- 06B. Demand by hour of day
-- ------------------------------------------------------------
SELECT
    created_hour,
    COUNT(*) AS request_count
FROM service_requests
WHERE created_hour IS NOT NULL
GROUP BY created_hour
ORDER BY created_hour;

-- ------------------------------------------------------------
-- 07. Distribution of recorded request statuses
-- ------------------------------------------------------------
SELECT
    COALESCE(status, 'Missing status') AS request_status,
    COUNT(*) AS request_count,
    ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM service_requests), 2)
        AS percentage_of_sample
FROM service_requests
GROUP BY status
ORDER BY request_count DESC;

-- ------------------------------------------------------------
-- 08. Descriptive statistics for valid resolution durations
-- Valid duration excludes records where closed_date precedes
-- created_date. Outliers are retained in these statistics.
-- ------------------------------------------------------------
WITH valid_durations AS (
    SELECT resolution_hours
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
)
SELECT
    COUNT(*) AS valid_duration_count,
    ROUND(AVG(resolution_hours), 4) AS mean_hours,
    ROUND(MIN(resolution_hours), 4) AS minimum_hours,
    ROUND(MAX(resolution_hours), 4) AS maximum_hours,
    ROUND(MAX(resolution_hours) - MIN(resolution_hours), 4) AS range_hours,
    ROUND(STDDEV_POP(resolution_hours), 4) AS population_stddev_hours,
    ROUND(VAR_POP(resolution_hours), 4) AS population_variance_hours,
    ROUND(AVG(resolution_hours * resolution_hours), 4) AS mean_square_hours
FROM valid_durations;

-- ------------------------------------------------------------
-- 08B. Median and quartiles for valid resolution durations
-- Uses row positions so it works without percentile functions.
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        resolution_hours,
        ROW_NUMBER() OVER (ORDER BY resolution_hours) AS rn,
        COUNT(*) OVER () AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
quartiles AS (
    SELECT
        MAX(CASE WHEN rn = CEIL(n * 0.25) THEN resolution_hours END) AS q1_hours,
        AVG(CASE WHEN rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
                 THEN resolution_hours END) AS median_hours,
        MAX(CASE WHEN rn = CEIL(n * 0.75) THEN resolution_hours END) AS q3_hours
    FROM ranked
)
SELECT
    ROUND(q1_hours, 4) AS q1_hours,
    ROUND(median_hours, 4) AS median_hours,
    ROUND(q3_hours, 4) AS q3_hours,
    ROUND(q3_hours - q1_hours, 4) AS iqr_hours
FROM quartiles;

-- ------------------------------------------------------------
-- 09. Median resolution duration by complaint category
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        complaint_type,
        resolution_hours,
        ROW_NUMBER() OVER (
            PARTITION BY complaint_type
            ORDER BY resolution_hours
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY complaint_type
        ) AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
medians AS (
    SELECT
        complaint_type,
        AVG(resolution_hours) AS median_resolution_hours,
        MAX(n) AS valid_closed_requests
    FROM ranked
    WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
    GROUP BY complaint_type
)
SELECT
    COALESCE(complaint_type, 'Missing complaint type') AS complaint_type,
    valid_closed_requests,
    ROUND(median_resolution_hours, 4) AS median_resolution_hours
FROM medians
ORDER BY median_resolution_hours DESC;

-- ------------------------------------------------------------
-- 10. Median resolution duration by agency
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        agency,
        agency_name,
        resolution_hours,
        ROW_NUMBER() OVER (
            PARTITION BY agency, agency_name
            ORDER BY resolution_hours
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY agency, agency_name
        ) AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
medians AS (
    SELECT
        agency,
        agency_name,
        AVG(resolution_hours) AS median_resolution_hours,
        MAX(n) AS valid_closed_requests
    FROM ranked
    WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
    GROUP BY agency, agency_name
)
SELECT
    COALESCE(agency, 'Missing agency') AS agency,
    COALESCE(agency_name, 'Missing agency name') AS agency_name,
    valid_closed_requests,
    ROUND(median_resolution_hours, 4) AS median_resolution_hours
FROM medians
ORDER BY median_resolution_hours DESC;

-- ------------------------------------------------------------
-- 11. IQR-based outlier investigation
-- Outlier rule: below Q1 - 1.5*IQR or above Q3 + 1.5*IQR.
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        resolution_hours,
        ROW_NUMBER() OVER (ORDER BY resolution_hours) AS rn,
        COUNT(*) OVER () AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
quartiles AS (
    SELECT
        MAX(CASE WHEN rn = CEIL(n * 0.25) THEN resolution_hours END) AS q1,
        MAX(CASE WHEN rn = CEIL(n * 0.75) THEN resolution_hours END) AS q3
    FROM ranked
),
bounds AS (
    SELECT
        q1,
        q3,
        q3 - q1 AS iqr,
        q1 - 1.5 * (q3 - q1) AS lower_bound,
        q3 + 1.5 * (q3 - q1) AS upper_bound
    FROM quartiles
)
SELECT
    COUNT(*) AS valid_duration_records,
    SUM(sr.resolution_hours < b.lower_bound) AS below_lower_bound,
    SUM(sr.resolution_hours > b.upper_bound) AS above_upper_bound,
    SUM(
        sr.resolution_hours < b.lower_bound
        OR sr.resolution_hours > b.upper_bound
    ) AS total_outliers,
    ROUND(b.q1, 4) AS q1_hours,
    ROUND(b.q3, 4) AS q3_hours,
    ROUND(b.iqr, 4) AS iqr_hours,
    ROUND(b.lower_bound, 4) AS lower_bound_hours,
    ROUND(b.upper_bound, 4) AS upper_bound_hours
FROM service_requests sr
CROSS JOIN bounds b
WHERE sr.resolution_hours IS NOT NULL
  AND sr.resolution_hours >= 0
GROUP BY b.q1, b.q3, b.iqr, b.lower_bound, b.upper_bound;

-- ------------------------------------------------------------
-- 11B. Sample of individual high-side outlier records
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        resolution_hours,
        ROW_NUMBER() OVER (ORDER BY resolution_hours) AS rn,
        COUNT(*) OVER () AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
quartiles AS (
    SELECT
        MAX(CASE WHEN rn = CEIL(n * 0.25) THEN resolution_hours END) AS q1,
        MAX(CASE WHEN rn = CEIL(n * 0.75) THEN resolution_hours END) AS q3
    FROM ranked
),
bounds AS (
    SELECT q3 + 1.5 * (q3 - q1) AS upper_bound
    FROM quartiles
)
SELECT
    sr.unique_key,
    sr.complaint_type,
    sr.agency,
    sr.borough,
    sr.created_date,
    sr.closed_date,
    sr.resolution_hours
FROM service_requests sr
CROSS JOIN bounds b
WHERE sr.resolution_hours > b.upper_bound
ORDER BY sr.resolution_hours DESC
LIMIT 100;

-- ------------------------------------------------------------
-- 12A. Resolution-time skewness
-- Population moment coefficient of skewness:
-- E[(X - mean)^3] / population_stddev^3
-- ------------------------------------------------------------
WITH moments AS (
    SELECT
        COUNT(*) AS n,
        AVG(resolution_hours) AS mean_hours,
        STDDEV_POP(resolution_hours) AS stddev_hours
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
third_moment AS (
    SELECT
        AVG(POW(sr.resolution_hours - m.mean_hours, 3)) AS moment_3,
        MAX(m.mean_hours) AS mean_hours,
        MAX(m.stddev_hours) AS stddev_hours,
        MAX(m.n) AS n
    FROM service_requests sr
    CROSS JOIN moments m
    WHERE sr.resolution_hours IS NOT NULL
      AND sr.resolution_hours >= 0
)
SELECT
    n AS valid_duration_count,
    ROUND(mean_hours, 4) AS mean_hours,
    ROUND(stddev_hours, 4) AS stddev_hours,
    ROUND(moment_3 / NULLIF(POW(stddev_hours, 3), 0), 4)
        AS population_skewness
FROM third_moment;

-- ------------------------------------------------------------
-- 12B. Resolution-duration bands for distribution interpretation
-- ------------------------------------------------------------
SELECT
    CASE
        WHEN resolution_hours < 1 THEN '< 1 hour'
        WHEN resolution_hours < 24 THEN '1 to < 24 hours'
        WHEN resolution_hours < 72 THEN '1 to < 3 days'
        WHEN resolution_hours < 168 THEN '3 to < 7 days'
        WHEN resolution_hours < 720 THEN '7 to < 30 days'
        ELSE '30 days or more'
    END AS resolution_band,
    COUNT(*) AS request_count
FROM service_requests
WHERE resolution_hours IS NOT NULL
  AND resolution_hours >= 0
GROUP BY resolution_band
ORDER BY MIN(resolution_hours);

-- ------------------------------------------------------------
-- 13. Geographic complaint concentration
-- 13A: Borough summaries, including missing/unavailable labels
-- ------------------------------------------------------------
SELECT
    COALESCE(borough, 'NULL / Missing') AS borough_group,
    COUNT(*) AS request_count,
    COUNT(latitude) AS records_with_latitude,
    COUNT(longitude) AS records_with_longitude,
    SUM(latitude IS NOT NULL AND longitude IS NOT NULL)
        AS records_with_both_coordinates
FROM service_requests
GROUP BY borough
ORDER BY request_count DESC;

-- 13B: Approximate coordinate-cell concentration
-- Rounded to 2 decimal places; use only valid coordinate ranges.
SELECT
    ROUND(latitude, 2) AS latitude_cell,
    ROUND(longitude, 2) AS longitude_cell,
    COUNT(*) AS request_count
FROM service_requests
WHERE latitude BETWEEN -90 AND 90
  AND longitude BETWEEN -180 AND 180
GROUP BY ROUND(latitude, 2), ROUND(longitude, 2)
ORDER BY request_count DESC
LIMIT 25;

-- ------------------------------------------------------------
-- 14. Recorded closure-rate comparison by agency
-- Closure means a recorded closed_date, not satisfaction.
-- ------------------------------------------------------------
SELECT
    COALESCE(agency, 'Missing agency') AS agency,
    COALESCE(agency_name, 'Missing agency name') AS agency_name,
    COUNT(*) AS total_requests,
    SUM(is_closed = TRUE) AS recorded_closed,
    SUM(is_closed = FALSE) AS not_recorded_closed,
    ROUND(100.0 * SUM(is_closed = TRUE) / COUNT(*), 2)
        AS recorded_closure_percentage
FROM service_requests
GROUP BY agency, agency_name
ORDER BY recorded_closure_percentage DESC, total_requests DESC;

-- 14B. Recorded closure-rate comparison by borough
SELECT
    COALESCE(borough, 'NULL / Missing') AS borough_group,
    COUNT(*) AS total_requests,
    SUM(is_closed = TRUE) AS recorded_closed,
    SUM(is_closed = FALSE) AS not_recorded_closed,
    ROUND(100.0 * SUM(is_closed = TRUE) / COUNT(*), 2)
        AS recorded_closure_percentage
FROM service_requests
GROUP BY borough
ORDER BY recorded_closure_percentage DESC;

-- ------------------------------------------------------------
-- 15. Categories with high request volume and long median
-- resolution duration.
-- High volume is defined here as categories in the top quartile
-- of category request counts. Long duration means the category
-- median is above the overall median. These are analytical
-- project thresholds, not official NYC performance standards.
-- ------------------------------------------------------------
WITH category_volume AS (
    SELECT
        complaint_type,
        COUNT(*) AS total_requests
    FROM service_requests
    GROUP BY complaint_type
),
category_median_ranked AS (
    SELECT
        complaint_type,
        resolution_hours,
        ROW_NUMBER() OVER (
            PARTITION BY complaint_type
            ORDER BY resolution_hours
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY complaint_type
        ) AS n
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
),
category_medians AS (
    SELECT
        complaint_type,
        AVG(resolution_hours) AS median_resolution_hours,
        MAX(n) AS valid_resolution_records
    FROM category_median_ranked
    WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
    GROUP BY complaint_type
),
volume_threshold AS (
    SELECT
        MIN(total_requests) AS top_quartile_minimum
    FROM (
        SELECT
            total_requests,
            ROW_NUMBER() OVER (ORDER BY total_requests) AS rn,
            COUNT(*) OVER () AS n
        FROM category_volume
    ) ranked_volumes
    WHERE rn = CEIL(n * 0.75)
),
overall_median AS (
    SELECT AVG(resolution_hours) AS median_hours
    FROM (
        SELECT
            resolution_hours,
            ROW_NUMBER() OVER (ORDER BY resolution_hours) AS rn,
            COUNT(*) OVER () AS n
        FROM service_requests
        WHERE resolution_hours IS NOT NULL
          AND resolution_hours >= 0
    ) ranked_all
    WHERE rn IN (FLOOR((n + 1) / 2), CEIL((n + 1) / 2))
)
SELECT
    COALESCE(v.complaint_type, 'Missing complaint type') AS complaint_type,
    v.total_requests,
    m.valid_resolution_records,
    ROUND(m.median_resolution_hours, 4) AS median_resolution_hours,
    ROUND(o.median_hours, 4) AS overall_median_hours
FROM category_volume v
JOIN category_medians m
    ON v.complaint_type <=> m.complaint_type
CROSS JOIN volume_threshold t
CROSS JOIN overall_median o
WHERE v.total_requests >= t.top_quartile_minimum
  AND m.median_resolution_hours > o.median_hours
ORDER BY v.total_requests DESC, m.median_resolution_hours DESC;

-- ============================================================
-- END OF 15 CORE INVESTIGATIONS
-- Notes:
-- * These results describe the 100,000-record project sample.
-- * The full source monthly volume is available separately in
--   reports/nyc311_monthly_source_counts_*.csv.
-- * Recorded closure does not prove citizen satisfaction.
-- * Outliers are flagged for investigation, not deleted.
-- ============================================================
