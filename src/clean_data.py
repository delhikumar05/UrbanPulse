
"""
URBAN PULSE
NYC 311 Data Cleaning and Feature Engineering

Input:
    data/raw/urban_pulse_nyc311_raw_*.csv

Outputs:
    data/processed/urban_pulse_nyc311_cleaned_*.csv
    reports/data_quality_report_*.csv
    reports/data_quality_summary_*.json

Raw files are never modified.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


# --------------------------------------------------
# 1. PROJECT PATHS
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
REPORT_DIR = PROJECT_ROOT / "reports"

PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------
# 2. CONFIGURATION
# --------------------------------------------------

DATE_COLUMNS = [
    "created_date",
    "closed_date",
    "due_date",
    "resolution_action_updated_date",
]

NUMERIC_COLUMNS = [
    "latitude",
    "longitude",
    "council_district",
]

REQUIRED_COLUMNS = [
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
]


# --------------------------------------------------
# 3. FIND LATEST RAW DATASET
# --------------------------------------------------

def find_latest_raw_file() -> Path:

    files = list(
        RAW_DIR.glob("urban_pulse_nyc311_raw_*.csv")
    )

    if not files:
        raise FileNotFoundError(
            "No NYC raw dataset found in data/raw."
        )

    return max(files, key=lambda file: file.stat().st_mtime)


# --------------------------------------------------
# 4. LOAD RAW DATA
# --------------------------------------------------

def load_raw_data(file_path: Path) -> pd.DataFrame:

    print("\nLoading raw NYC 311 dataset...")

    df = pd.read_csv(
        file_path,
        dtype={
            "unique_key": "string",
            "incident_zip": "string",
        },
        low_memory=False
    )

    print("Raw records:", f"{len(df):,}")
    print("Raw columns:", len(df.columns))

    missing_columns = set(REQUIRED_COLUMNS) - set(df.columns)

    if missing_columns:
        raise ValueError(
            f"Required source fields missing: {sorted(missing_columns)}"
        )

    return df


# --------------------------------------------------
# 5. CLEAN TEXT FIELDS
# --------------------------------------------------

def clean_text_fields(df: pd.DataFrame) -> pd.DataFrame:

    print("\nCleaning text fields...")

    text_columns = df.select_dtypes(
        include=["object", "string"]
    ).columns

    for column in text_columns:

        df[column] = (
            df[column]
            .astype("string")
            .str.strip()
        )

        df[column] = df[column].replace("", pd.NA)

    return df


# --------------------------------------------------
# 6. CONVERT DATE COLUMNS
# --------------------------------------------------

def convert_date_columns(df: pd.DataFrame) -> pd.DataFrame:

    print("\nConverting date fields...")

    for column in DATE_COLUMNS:

        original_values = df[column].copy()

        has_value = (
            original_values.notna()
            & original_values.astype("string").str.strip().ne("")
        )

        parsed_dates = pd.to_datetime(
            original_values,
            errors="coerce",
            format="mixed"
        )

        df[column] = parsed_dates

        # Flag values that existed but could not be parsed.
        flag_name = f"invalid_{column}"

        df[flag_name] = (
            has_value & parsed_dates.isna()
        )

        invalid_count = int(df[flag_name].sum())

        print(
            f"{column}: {invalid_count} invalid values"
        )

    return df


# --------------------------------------------------
# 7. CONVERT NUMERIC FIELDS
# --------------------------------------------------

def convert_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:

    print("\nConverting numeric fields...")

    for column in NUMERIC_COLUMNS:

        original_values = df[column].copy()

        has_value = (
            original_values.notna()
            & original_values.astype("string").str.strip().ne("")
        )

        converted = pd.to_numeric(
            original_values,
            errors="coerce"
        )

        df[column] = converted

        df[f"invalid_{column}"] = (
            has_value & converted.isna()
        )

    return df


# --------------------------------------------------
# 8. VALIDATE COORDINATES
# --------------------------------------------------

def validate_coordinates(df: pd.DataFrame) -> pd.DataFrame:

    print("\nValidating coordinates...")

    df["invalid_latitude_range"] = (
        df["latitude"].notna()
        & ~df["latitude"].between(-90, 90)
    )

    df["invalid_longitude_range"] = (
        df["longitude"].notna()
        & ~df["longitude"].between(-180, 180)
    )

    df["incomplete_coordinates"] = (
        df["latitude"].isna()
        | df["longitude"].isna()
    )

    df["invalid_coordinates"] = (
        df["invalid_latitude"]
        | df["invalid_longitude"]
        | df["invalid_latitude_range"]
        | df["invalid_longitude_range"]
    )

    print(
        "Invalid coordinate records:",
        int(df["invalid_coordinates"].sum())
    )

    return df


# --------------------------------------------------
# 9. CHECK DUPLICATE REQUEST IDS
# --------------------------------------------------

def check_duplicates(df: pd.DataFrame) -> pd.DataFrame:

    print("\nChecking duplicate request IDs...")

    df["duplicate_request_id"] = (
        df["unique_key"].notna()
        & df["unique_key"].duplicated(keep=False)
    )

    duplicates = int(
        df["unique_key"].notna().sum()
        - df["unique_key"].nunique()
    )

    missing_ids = int(
        df["unique_key"].isna().sum()
    )

    print("Duplicate ID excess:", duplicates)
    print("Missing request IDs:", missing_ids)

    # Do not automatically delete records.
    return df


# --------------------------------------------------
# 10. FEATURE ENGINEERING
# --------------------------------------------------

def create_derived_features(df: pd.DataFrame) -> pd.DataFrame:

    print("\nCreating derived fields...")

    # Closure indicator based on a valid recorded closed date.
    df["is_closed"] = df["closed_date"].notna()

    # Validate creation and closure date order.
    df["closed_before_created"] = (
        df["created_date"].notna()
        & df["closed_date"].notna()
        & (df["closed_date"] < df["created_date"])
    )

    # Calculate duration only for valid date combinations.
    duration = (
        df["closed_date"] - df["created_date"]
    ).dt.total_seconds() / 3600

    valid_duration = (
        df["created_date"].notna()
        & df["closed_date"].notna()
        & ~df["closed_before_created"]
    )

    df["resolution_hours"] = duration.where(
        valid_duration
    )

    # Creation date grouping fields.
    df["created_year"] = (
        df["created_date"].dt.year
    )

    df["created_month"] = (
        df["created_date"].dt.month
    )

    df["created_weekday"] = (
        df["created_date"].dt.day_name()
    )

    df["created_hour"] = (
        df["created_date"].dt.hour
    )

    print(
        "Valid resolution durations:",
        int(df["resolution_hours"].notna().sum())
    )

    return df


# --------------------------------------------------
# 11. GENERATE DATA QUALITY REPORT
# --------------------------------------------------

def create_quality_report(
    df: pd.DataFrame,
    timestamp: str
) -> dict:

    print("\nCreating data quality report...")

    report = pd.DataFrame({

        "column_name": df.columns,

        "data_type": [
            str(df[column].dtype)
            for column in df.columns
        ],

        "missing_count": [
            int(df[column].isna().sum())
            for column in df.columns
        ],

        "missing_percentage": [
            round(
                float(df[column].isna().mean() * 100),
                2
            )
            for column in df.columns
        ],

        "distinct_values": [
            int(df[column].nunique())
            for column in df.columns
        ],

    })

    report_file = (
        REPORT_DIR
        / f"data_quality_report_{timestamp}.csv"
    )

    report.to_csv(
        report_file,
        index=False,
        encoding="utf-8-sig"
    )

    summary = {

        "project": "Urban Pulse",

        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "total_records": len(df),

        "total_columns": len(df.columns),

        "unique_request_ids": int(
            df["unique_key"].nunique()
        ),

        "duplicate_request_id_excess": int(
            df["unique_key"].notna().sum()
            - df["unique_key"].nunique()
        ),

        "missing_request_ids": int(
            df["unique_key"].isna().sum()
        ),

        "closed_requests": int(
            df["is_closed"].sum()
        ),

        "open_requests": int(
            (~df["is_closed"]).sum()
        ),

        "invalid_creation_dates": int(
            df["invalid_created_date"].sum()
        ),

        "invalid_closure_dates": int(
            df["invalid_closed_date"].sum()
        ),

        "closed_before_created": int(
            df["closed_before_created"].sum()
        ),

        "invalid_coordinates": int(
            df["invalid_coordinates"].sum()
        ),

        "valid_resolution_durations": int(
            df["resolution_hours"].notna().sum()
        ),

        "columns_with_missing_values": int(
            (df.isna().sum() > 0).sum()
        ),

        "quality_report_file": report_file.name,
    }

    summary_file = (
        REPORT_DIR
        / f"data_quality_summary_{timestamp}.json"
    )

    with open(
        summary_file,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            summary,
            file,
            indent=4
        )

    return summary


# --------------------------------------------------
# 12. SAVE CLEANED DATASET
# --------------------------------------------------

def save_cleaned_data(
    df: pd.DataFrame,
    timestamp: str
) -> Path:

    output_file = (
        PROCESSED_DIR
        / f"urban_pulse_nyc311_cleaned_{timestamp}.csv"
    )

    df.to_csv(
        output_file,
        index=False,
        encoding="utf-8-sig"
    )

    return output_file


# --------------------------------------------------
# 13. MAIN EXECUTION
# --------------------------------------------------

def main():

    timestamp = datetime.now(
        timezone.utc
    ).strftime("%Y%m%d_%H%M%S")

    print("=" * 65)
    print("URBAN PULSE - DATA CLEANING")
    print("=" * 65)

    raw_file = find_latest_raw_file()

    print("\nSelected raw file:", raw_file.name)

    df = load_raw_data(raw_file)

    original_record_count = len(df)

    df = clean_text_fields(df)

    df = convert_date_columns(df)

    df = convert_numeric_columns(df)

    df = validate_coordinates(df)

    df = check_duplicates(df)

    df = create_derived_features(df)

    if len(df) != original_record_count:
        raise RuntimeError(
            "Record count changed unexpectedly during cleaning."
        )

    summary = create_quality_report(
        df,
        timestamp
    )

    output_file = save_cleaned_data(
        df,
        timestamp
    )

    print("\n" + "=" * 65)
    print("DATA CLEANING COMPLETED")
    print("=" * 65)

    print("Original records:", f"{original_record_count:,}")

    print("Cleaned records:", f"{len(df):,}")

    print("Cleaned columns:", len(df.columns))

    print("Cleaned dataset:", output_file)

    print("\nDATA QUALITY SUMMARY")

    for key, value in summary.items():
        print(f"{key}: {value}")

    print("\nRaw dataset has not been modified.")


if __name__ == "__main__":
    try:
        main()

    except Exception as error:
        print("\nDATA CLEANING FAILED")
        print("Reason:", error)
        raise
