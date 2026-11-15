USE urban_pulse_db;

-- 1. Baseline totals used by the unfiltered dashboard
SELECT
    COUNT(DISTINCT unique_key) AS total_requests,
    COALESCE(SUM(is_closed = TRUE), 0) AS recorded_closed,
    COUNT(*) - COALESCE(SUM(is_closed = TRUE), 0) AS open_requests,
    ROUND(
        100.0 * COALESCE(SUM(is_closed = TRUE), 0) / NULLIF(COUNT(*), 0),
        2
    ) AS recorded_closure_percentage
FROM service_requests;

-- 2. Resolution KPI validation: valid duration count and share over 72 hours
SELECT
    COUNT(*) AS valid_resolution_records,
    SUM(resolution_hours > 72) AS over_72_hour_records,
    ROUND(
        100.0 * SUM(resolution_hours > 72) / NULLIF(COUNT(*), 0),
        2
    ) AS over_72_hour_percentage
FROM service_requests
WHERE resolution_hours IS NOT NULL
  AND resolution_hours >= 0;

-- 3. Exact median resolution hours using MySQL window functions
WITH ranked_durations AS (
    SELECT
        resolution_hours,
        ROW_NUMBER() OVER (ORDER BY resolution_hours) AS row_num,
        COUNT(*) OVER () AS total_rows
    FROM service_requests
    WHERE resolution_hours IS NOT NULL
      AND resolution_hours >= 0
)
SELECT ROUND(AVG(resolution_hours), 4) AS median_resolution_hours
FROM ranked_durations
WHERE row_num IN (
    FLOOR((total_rows + 1) / 2),
    FLOOR((total_rows + 2) / 2)
);

-- 4. Brooklyn filter validation
SELECT
    COUNT(DISTINCT unique_key) AS total_requests,
    COALESCE(SUM(is_closed = TRUE), 0) AS recorded_closed,
    COUNT(*) - COALESCE(SUM(is_closed = TRUE), 0) AS open_requests,
    ROUND(
        100.0 * COALESCE(SUM(is_closed = TRUE), 0) / NULLIF(COUNT(*), 0),
        2
    ) AS recorded_closure_percentage
FROM service_requests
WHERE borough = 'BROOKLYN';

-- 5. Noise - Residential filter validation
SELECT
    COUNT(DISTINCT unique_key) AS total_requests
FROM service_requests
WHERE complaint_type = 'Noise - Residential';

-- 6. November 2026 date filter validation
SELECT
    COUNT(DISTINCT unique_key) AS total_requests,
    COALESCE(SUM(is_closed = TRUE), 0) AS recorded_closed,
    COUNT(*) - COALESCE(SUM(is_closed = TRUE), 0) AS open_requests
FROM service_requests
WHERE created_date >= '2026-11-01 00:00:00'
  AND created_date <  '2026-11-15 00:00:00';
