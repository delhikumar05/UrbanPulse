
CREATE DATABASE IF NOT EXISTS urban_pulse_db
CHARACTER SET utf8mb4
COLLATE utf8mb4_0900_ai_ci;

USE urban_pulse_db;


-- ==========================================
-- MAIN TABLE: service_requests
-- ==========================================

CREATE TABLE IF NOT EXISTS service_requests
(
    -- Original NYC 311 fields (25)

    unique_key VARCHAR(50) NOT NULL,
    created_date DATETIME(6),
    closed_date DATETIME(6),
    due_date DATETIME(6),
    agency VARCHAR(50),
    agency_name VARCHAR(255),
    complaint_type VARCHAR(255),
    descriptor VARCHAR(500),
    location_type VARCHAR(255),
    incident_zip VARCHAR(20),
    incident_address VARCHAR(500),
    street_name VARCHAR(255),
    city VARCHAR(150),
    status VARCHAR(100),
    borough VARCHAR(100),
    community_board VARCHAR(100),
    council_district DECIMAL(10,2),
    police_precinct VARCHAR(100),
    open_data_channel_type VARCHAR(100),
    resolution_description TEXT,
    resolution_action_updated_date DATETIME(6),
    latitude DECIMAL(10,7),
    longitude DECIMAL(10,7),
    park_facility_name VARCHAR(255),
    park_borough VARCHAR(100),

    -- Date validation flags (4)

    invalid_created_date BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_closed_date BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_due_date BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_resolution_action_updated_date BOOLEAN
        NOT NULL DEFAULT FALSE,

    -- Numeric validation flags (3)

    invalid_latitude BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_longitude BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_council_district BOOLEAN NOT NULL DEFAULT FALSE,

    -- Coordinate validation (4)

    invalid_latitude_range BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_longitude_range BOOLEAN NOT NULL DEFAULT FALSE,
    incomplete_coordinates BOOLEAN NOT NULL DEFAULT FALSE,
    invalid_coordinates BOOLEAN NOT NULL DEFAULT FALSE,

    -- Duplicate validation (1)

    duplicate_request_id BOOLEAN NOT NULL DEFAULT FALSE,

    -- Derived fields (7)

    is_closed BOOLEAN NOT NULL DEFAULT FALSE,
    closed_before_created BOOLEAN NOT NULL DEFAULT FALSE,
    resolution_hours DECIMAL(18,4),
    created_year SMALLINT,
    created_month TINYINT,
    created_weekday VARCHAR(20),
    created_hour TINYINT,

    -- Primary key

    PRIMARY KEY (unique_key),

    -- Analytical indexes

    INDEX idx_created_date (created_date),
    INDEX idx_closed_date (closed_date),
    INDEX idx_complaint_type (complaint_type),
    INDEX idx_agency (agency),
    INDEX idx_borough (borough),
    INDEX idx_status (status)

) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci;


-- ==========================================
-- STAGING TABLE
-- ==========================================

CREATE TABLE IF NOT EXISTS service_requests_staging
LIKE service_requests;


-- ==========================================
-- VERIFY DATABASE OBJECTS
-- ==========================================

SHOW TABLES;

DESCRIBE service_requests;

DESCRIBE service_requests_staging;
