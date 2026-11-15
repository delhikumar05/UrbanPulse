"""
Urban Pulse - Load cleaned NYC 311 data into MySQL.

Workflow:
1. Find the newest cleaned CSV in data/processed.
2. Validate the CSV columns against the MySQL staging table.
3. Load the CSV into service_requests_staging in batches.
4. Verify row count, unique request IDs, and missing IDs.
5. Replace service_requests from the validated staging table in a transaction.
6. Verify the published table.

Credentials are read from the project-root .env file. The script never prints
or logs the MySQL password or NYC application token.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import mysql.connector
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from mysql.connector import Error


# -----------------------------------------------------------------------------
# 1. PROJECT CONFIGURATION
# -----------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
BATCH_SIZE = 1_000

load_dotenv(PROJECT_ROOT / ".env")

MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "urban_pulse_db"),
    "charset": "utf8mb4",
    "use_unicode": True,
    "autocommit": False,
}

TABLE_MAIN = "service_requests"
TABLE_STAGING = "service_requests_staging"

DATE_COLUMNS = {
    "created_date",
    "closed_date",
    "due_date",
    "resolution_action_updated_date",
}

BOOLEAN_COLUMNS = {
    "invalid_created_date",
    "invalid_closed_date",
    "invalid_due_date",
    "invalid_resolution_action_updated_date",
    "invalid_latitude",
    "invalid_longitude",
    "invalid_council_district",
    "invalid_latitude_range",
    "invalid_longitude_range",
    "incomplete_coordinates",
    "invalid_coordinates",
    "duplicate_request_id",
    "is_closed",
    "closed_before_created",
}

INTEGER_COLUMNS = {
    "created_year",
    "created_month",
    "created_hour",
}

EXPECTED_COLUMNS = [
    # Original NYC 311 fields (25)
    "unique_key",
    "created_date",
    "closed_date",
    "due_date",
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "location_type",
    "incident_zip",
    "incident_address",
    "street_name",
    "city",
    "status",
    "borough",
    "community_board",
    "council_district",
    "police_precinct",
    "open_data_channel_type",
    "resolution_description",
    "resolution_action_updated_date",
    "latitude",
    "longitude",
    "park_facility_name",
    "park_borough",
    # Date validation flags (4)
    "invalid_created_date",
    "invalid_closed_date",
    "invalid_due_date",
    "invalid_resolution_action_updated_date",
    # Numeric validation flags (3)
    "invalid_latitude",
    "invalid_longitude",
    "invalid_council_district",
    # Coordinate validation flags (4)
    "invalid_latitude_range",
    "invalid_longitude_range",
    "incomplete_coordinates",
    "invalid_coordinates",
    # Duplicate validation flag (1)
    "duplicate_request_id",
    # Derived fields (7)
    "is_closed",
    "closed_before_created",
    "resolution_hours",
    "created_year",
    "created_month",
    "created_weekday",
    "created_hour",
]


# -----------------------------------------------------------------------------
# 2. FILE AND SCHEMA VALIDATION
# -----------------------------------------------------------------------------

def find_latest_cleaned_file() -> Path:
    files = list(PROCESSED_DIR.glob("urban_pulse_nyc311_cleaned_*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No cleaned CSV found in {PROCESSED_DIR}. Run src/clean_data.py first."
        )
    return max(files, key=lambda file: file.stat().st_mtime)


def validate_identifier(identifier: str) -> str:
    """Allow only simple SQL identifiers before interpolating table/column names."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return f"`{identifier}`"


def get_table_columns(cursor, table_name: str) -> list[str]:
    safe_table = validate_identifier(table_name)
    cursor.execute(f"SHOW COLUMNS FROM {safe_table}")
    return [row[0] for row in cursor.fetchall()]


def validate_csv_and_tables(csv_columns: list[str], cursor) -> None:
    if csv_columns != EXPECTED_COLUMNS:
        missing = sorted(set(EXPECTED_COLUMNS) - set(csv_columns))
        extra = sorted(set(csv_columns) - set(EXPECTED_COLUMNS))
        order_differs = not missing and not extra
        raise ValueError(
            "Cleaned CSV columns do not match the expected 44-column schema. "
            f"Missing={missing}; Extra={extra}; Order differs={order_differs}. "
            "Do not load until the schema is reconciled."
        )

    for table_name in (TABLE_STAGING, TABLE_MAIN):
        table_columns = get_table_columns(cursor, table_name)
        if table_columns != csv_columns:
            missing = sorted(set(csv_columns) - set(table_columns))
            extra = sorted(set(table_columns) - set(csv_columns))
            raise ValueError(
                f"MySQL table {table_name} does not match the cleaned CSV. "
                f"Missing={missing}; Extra={extra}; "
                f"Column order matches={table_columns == csv_columns}. "
                "Recreate/adjust the tables using the project SQL schema before loading."
            )


# -----------------------------------------------------------------------------
# 3. VALUE CONVERSION
# -----------------------------------------------------------------------------

def is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if np.isscalar(result) else False
    except (TypeError, ValueError):
        return False


def convert_value(column: str, value: Any) -> Any:
    """Convert pandas/numpy values to mysql-connector compatible Python values."""
    if is_missing(value):
        return None

    if column in DATE_COLUMNS:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        # MySQL DATETIME stores a timezone-naive datetime. Source dates in this
        # dataset are supplied without a timezone offset.
        if getattr(parsed, "tzinfo", None) is not None:
            parsed = parsed.tz_convert(None)
        return parsed.to_pydatetime()

    if column in BOOLEAN_COLUMNS:
        if isinstance(value, (bool, np.bool_)):
            return bool(value)
        normalized = str(value).strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
        raise ValueError(f"Cannot convert {value!r} to boolean for {column}")

    if column in INTEGER_COLUMNS:
        return int(value)

    if isinstance(value, np.generic):
        return value.item()

    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    return value


# -----------------------------------------------------------------------------
# 4. DATABASE CONNECTION AND LOADING
# -----------------------------------------------------------------------------

def create_connection():
    if not MYSQL_CONFIG["password"]:
        raise ValueError(
            "MYSQL_PASSWORD is empty. Add MYSQL_PASSWORD to the project-root .env file."
        )
    return mysql.connector.connect(**MYSQL_CONFIG)


def load_csv_to_staging(csv_path: Path, connection, columns: list[str]) -> int:
    safe_columns = ", ".join(validate_identifier(col) for col in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = (
        f"INSERT INTO {validate_identifier(TABLE_STAGING)} "
        f"({safe_columns}) VALUES ({placeholders})"
    )

    total_inserted = 0
    batch_number = 0
    cursor = connection.cursor()

    try:
        # Staging is disposable/reloadable. The main table is not touched here.
        cursor.execute(f"TRUNCATE TABLE {validate_identifier(TABLE_STAGING)}")
        connection.commit()

        reader = pd.read_csv(
            csv_path,
            dtype={"unique_key": "string", "incident_zip": "string"},
            chunksize=BATCH_SIZE,
            low_memory=False,
        )

        for chunk in reader:
            if list(chunk.columns) != columns:
                raise ValueError("CSV column order changed while reading chunks.")

            rows = [
                tuple(convert_value(col, value) for col, value in zip(columns, row))
                for row in chunk.itertuples(index=False, name=None)
            ]

            cursor.executemany(insert_sql, rows)
            connection.commit()

            inserted = len(rows)
            total_inserted += inserted
            batch_number += 1

            print(
                f"Batch {batch_number}: inserted {inserted:,} rows | "
                f"Total staged: {total_inserted:,}"
            )

    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()

    return total_inserted


# -----------------------------------------------------------------------------
# 5. VALIDATION AND SAFE PUBLISH
# -----------------------------------------------------------------------------

def get_table_metrics(cursor, table_name: str) -> tuple[int, int, int]:
    safe_table = validate_identifier(table_name)
    cursor.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT `unique_key`), "
        f"COALESCE(SUM(`unique_key` IS NULL), 0) FROM {safe_table}"
    )
    row = cursor.fetchone()
    return int(row[0]), int(row[1]), int(row[2])


def publish_staging(connection, columns: list[str], expected_count: int) -> None:
    safe_columns = ", ".join(validate_identifier(col) for col in columns)
    cursor = connection.cursor()

    try:
        connection.start_transaction()
        cursor.execute(f"DELETE FROM {validate_identifier(TABLE_MAIN)}")
        cursor.execute(
            f"INSERT INTO {validate_identifier(TABLE_MAIN)} ({safe_columns}) "
            f"SELECT {safe_columns} FROM {validate_identifier(TABLE_STAGING)}"
        )

        main_count, main_unique, main_null_ids = get_table_metrics(
            cursor, TABLE_MAIN
        )
        if main_count != expected_count or main_unique != expected_count or main_null_ids:
            raise RuntimeError(
                "Published table validation failed: "
                f"rows={main_count:,}, unique_ids={main_unique:,}, "
                f"missing_ids={main_null_ids:,}, expected={expected_count:,}."
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


# -----------------------------------------------------------------------------
# 6. MAIN
# -----------------------------------------------------------------------------

def main() -> None:
    if MYSQL_CONFIG["database"] != "urban_pulse_db":
        raise ValueError(
            "MYSQL_DATABASE must be urban_pulse_db for this project."
        )

    csv_path = find_latest_cleaned_file()
    expected_count = sum(1 for _ in open(csv_path, "r", encoding="utf-8-sig")) - 1
    if expected_count < 1:
        raise ValueError(f"The cleaned CSV is empty: {csv_path}")

    print("=" * 70)
    print("URBAN PULSE - MYSQL DATA LOADER")
    print("=" * 70)
    print(f"Selected cleaned CSV: {csv_path.name}")
    print(f"Expected CSV records: {expected_count:,}")
    print(f"Batch size: {BATCH_SIZE:,}")

    connection = None
    try:
        connection = create_connection()
        print("\nMySQL connection successful.")

        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE(), VERSION()")
        database_name, mysql_version = cursor.fetchone()
        print(f"Database: {database_name}")
        print(f"MySQL version: {mysql_version}")

        # Use the CSV header to verify its exact column order.
        csv_columns = list(
            pd.read_csv(csv_path, nrows=0).columns
        )
        validate_csv_and_tables(csv_columns, cursor)
        print(f"Schema validation successful: {len(csv_columns)} columns.")
        cursor.close()

        inserted = load_csv_to_staging(csv_path, connection, csv_columns)
        print(f"\nStaging load completed: {inserted:,} rows.")

        cursor = connection.cursor()
        staging_count, staging_unique, staging_null_ids = get_table_metrics(
            cursor, TABLE_STAGING
        )
        print("\nSTAGING VALIDATION")
        print(f"CSV expected rows: {expected_count:,}")
        print(f"Rows in staging: {staging_count:,}")
        print(f"Unique request IDs: {staging_unique:,}")
        print(f"Missing request IDs: {staging_null_ids:,}")

        if (
            inserted != expected_count
            or staging_count != expected_count
            or staging_unique != expected_count
            or staging_null_ids != 0
        ):
            raise RuntimeError(
                "Staging validation failed. Main table has not been replaced."
            )
        cursor.close()
        # End the read transaction opened by staging validation before
        # starting the explicit publish transaction.
        connection.commit()

        print("\nStaging validation passed. Publishing to main table...")
        publish_staging(connection, csv_columns, expected_count)

        cursor = connection.cursor()
        main_count, main_unique, main_null_ids = get_table_metrics(
            cursor, TABLE_MAIN
        )
        cursor.execute(
            f"SELECT MIN(`created_date`), MAX(`created_date`), "
            f"SUM(`is_closed`), SUM(NOT `is_closed`) "
            f"FROM {validate_identifier(TABLE_MAIN)}"
        )
        min_date, max_date, closed_count, open_count = cursor.fetchone()
        cursor.close()

        print("\n" + "=" * 70)
        print("MYSQL DATA LOAD COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print(f"CSV records: {expected_count:,}")
        print(f"Inserted rows: {inserted:,}")
        print(f"Staging rows: {staging_count:,}")
        print(f"Main table rows: {main_count:,}")
        print(f"Unique request IDs: {main_unique:,}")
        print(f"Missing request IDs: {main_null_ids:,}")
        print(f"Earliest created date: {min_date}")
        print(f"Latest created date: {max_date}")
        print(f"Closed requests: {int(closed_count or 0):,}")
        print(f"Open requests: {int(open_count or 0):,}")
        print("Validation: PASSED")

    finally:
        if connection is not None and connection.is_connected():
            connection.close()
            print("\nMySQL connection closed.")


if __name__ == "__main__":
    try:
        main()
    except (Error, Exception) as error:
        print("\nMYSQL DATA LOAD FAILED")
        print(f"Reason: {error}")
        sys.exit(1)