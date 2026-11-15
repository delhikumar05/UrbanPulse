"""\
URBAN PULSE - NYC 311 Historical Data Extraction

This script:
1. Reads the NYC Open Data application token from the project .env file.
2. Queries the NYC 311 dataset for monthly source-record counts.
3. Allocates the requested sample proportionally across months.
4. Selects pages spread through each month and fetches fallback pages
   when a response returns fewer records than expected.
5. Enforces each month's allocated quota and the overall target.
6. Saves a timestamped raw CSV, monthly sampling report, and metadata JSON.

The script never modifies or deletes existing raw or cleaned datasets.

Expected project layout:
    UrbanPulse/
        .env
        data/raw/
        reports/
        src/extract_data.py

.env example:
    NYC_APP_TOKEN=your_application_token
    TARGET_RECORDS=100000
    PAGE_SIZE=250

Do not share your .env file or application token.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ---------------------------------------------------------------------
# 1. PROJECT CONFIGURATION
# ---------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
REPORT_DIR = PROJECT_ROOT / "reports"

RAW_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / ".env")

DATASET_ID = "erm2-nwe9"
DATASET_URL = (
    "https://data.cityofnewyork.us/Social-Services/"
    "311-Service-Requests-from-2020-to-Present/erm2-nwe9"
)
API_JSON_URL = (
    f"https://data.cityofnewyork.us/api/v3/views/{DATASET_ID}/query.json"
)
API_CSV_URL = (
    f"https://data.cityofnewyork.us/api/v3/views/{DATASET_ID}/query.csv"
)

APP_TOKEN = os.getenv("NYC_APP_TOKEN", "").strip()

try:
    TARGET_RECORDS = int(os.getenv("TARGET_RECORDS", "100000"))
    PAGE_SIZE = int(os.getenv("PAGE_SIZE", "250"))
except ValueError as exc:
    raise ValueError(
        "TARGET_RECORDS and PAGE_SIZE in .env must be whole numbers."
    ) from exc

START_DATE = "2020-01-01T00:00:00"
REQUEST_TIMEOUT = (30, 180)
REQUEST_PAUSE_SECONDS = 0.20

# The extraction is restricted to the fields requested in the GUVI brief.
FIELDS = [
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


# ---------------------------------------------------------------------
# 2. API SESSION WITH RETRIES
# ---------------------------------------------------------------------

def create_session() -> requests.Session:
    """Create a requests session with retries for temporary API failures."""

    retry_strategy = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["POST"]),
        respect_retry_after_header=True,
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.headers.update(
        {
            "X-App-Token": APP_TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json, text/csv",
            "User-Agent": "UrbanPulse-GUVI-AIML-Project",
        }
    )
    return session


def raise_for_api_error(response: requests.Response, operation: str) -> None:
    """Raise a useful error without printing credentials."""

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:1200] if response.text else "<empty response>"
        raise RuntimeError(
            f"NYC API error during {operation}. "
            f"HTTP {response.status_code}. Response: {body}"
        ) from exc


# ---------------------------------------------------------------------
# 3. RETRIEVE SOURCE COUNTS FOR EACH MONTH
# ---------------------------------------------------------------------

def get_monthly_source_counts(
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Return the number of source records available in each month."""

    print("\nFetching monthly source-record counts from NYC Open Data...")

    query = (
        "SELECT date_trunc_ym(created_date) AS month, count(*) AS total "
        f"WHERE created_date >= '{START_DATE}' "
        "GROUP BY month ORDER BY month ASC"
    )

    response = session.post(
        API_JSON_URL,
        json={"query": query, "includeSynthetic": False},
        timeout=REQUEST_TIMEOUT,
    )
    raise_for_api_error(response, "monthly source-count query")

    rows = response.json()
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(
            "The monthly source-count query returned no rows. "
            "Check the dataset endpoint and API response."
        )

    monthly_data: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("month") or row.get("total") is None:
            continue

        month = pd.to_datetime(row["month"], errors="coerce")
        if pd.isna(month):
            continue

        source_count = int(float(row["total"]))
        if source_count > 0:
            monthly_data.append(
                {
                    "month": month.to_period("M").to_timestamp(),
                    "source_count": source_count,
                }
            )

    monthly_data.sort(key=lambda item: item["month"])

    if not monthly_data:
        raise RuntimeError("No valid monthly source counts were returned.")

    source_total = sum(item["source_count"] for item in monthly_data)
    print(f"Months available: {len(monthly_data)}")
    print(f"Source records across available months: {source_total:,}")
    print(
        "Source date range: "
        f"{monthly_data[0]['month']:%Y-%m} through "
        f"{monthly_data[-1]['month']:%Y-%m}"
    )

    return monthly_data


# ---------------------------------------------------------------------
# 4. ALLOCATE THE TARGET PROPORTIONALLY BY MONTH
# ---------------------------------------------------------------------

def allocate_monthly_quotas(
    monthly_data: list[dict[str, Any]], target_records: int
) -> int:
    """Add sample_target to each month using the largest-remainder method."""

    total_available = sum(item["source_count"] for item in monthly_data)
    actual_target = min(target_records, total_available)

    if total_available <= 0 or actual_target <= 0:
        raise RuntimeError("The dataset has no available records to extract.")

    exact_quotas = [
        item["source_count"] / total_available * actual_target
        for item in monthly_data
    ]
    quotas = [math.floor(value) for value in exact_quotas]
    remaining = actual_target - sum(quotas)

    remainder_order = sorted(
        range(len(exact_quotas)),
        key=lambda index: exact_quotas[index] - quotas[index],
        reverse=True,
    )

    for index in remainder_order[:remaining]:
        quotas[index] += 1

    for item, quota in zip(monthly_data, quotas):
        item["sample_target"] = min(int(quota), item["source_count"])

    allocated_total = sum(item["sample_target"] for item in monthly_data)
    if allocated_total != actual_target:
        raise RuntimeError(
            "Monthly quota allocation did not equal the overall target: "
            f"allocated={allocated_total:,}, target={actual_target:,}."
        )

    return actual_target


# ---------------------------------------------------------------------
# 5. CHOOSE PAGE NUMBERS DISTRIBUTED THROUGH A MONTH
# ---------------------------------------------------------------------

def select_page_numbers(total_pages: int, pages_needed: int) -> list[int]:
    """Choose 1-indexed pages spread across the full month result set."""

    if total_pages <= 0 or pages_needed <= 0:
        return []

    pages_needed = min(total_pages, pages_needed)

    if pages_needed == total_pages:
        return list(range(1, total_pages + 1))

    if pages_needed == 1:
        return [(total_pages + 1) // 2]

    selected: list[int] = []
    for index in range(pages_needed):
        position = 1 + index * (total_pages - 1) / (pages_needed - 1)
        page_number = int(round(position))
        if page_number not in selected:
            selected.append(page_number)

    # Defensive fill in case rounding produced fewer unique page numbers.
    if len(selected) < pages_needed:
        for page_number in range(1, total_pages + 1):
            if page_number not in selected:
                selected.append(page_number)
                if len(selected) == pages_needed:
                    break

    return sorted(selected)


# ---------------------------------------------------------------------
# 6. FETCH ONE PAGE FOR A MONTH
# ---------------------------------------------------------------------

def fetch_month_page(
    session: requests.Session,
    query: str,
    page_number: int,
    page_size: int,
) -> pd.DataFrame:
    """Fetch one CSV page from the SODA 3 query endpoint."""

    response = session.post(
        API_CSV_URL,
        json={
            "query": query,
            "page": {
                "pageNumber": page_number,
                "pageSize": page_size,
            },
            "includeSynthetic": False,
        },
        timeout=REQUEST_TIMEOUT,
    )
    raise_for_api_error(response, f"monthly page {page_number}")

    csv_content = response.content.decode("utf-8-sig")
    if not csv_content.strip():
        return pd.DataFrame(columns=FIELDS)

    try:
        df = pd.read_csv(
            StringIO(csv_content),
            dtype={"unique_key": "string", "incident_zip": "string"},
            keep_default_na=False,
        )
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=FIELDS)

    df.columns = [str(column).strip() for column in df.columns]
    missing_fields = set(FIELDS) - set(df.columns)
    if missing_fields:
        raise RuntimeError(
            "The API response is missing expected fields: "
            f"{sorted(missing_fields)}"
        )

    return df[FIELDS].copy()


# ---------------------------------------------------------------------
# 7. EXTRACT ONE MONTH AND ENFORCE ITS QUOTA
# ---------------------------------------------------------------------

def extract_month_sample(
    session: requests.Session,
    item: dict[str, Any],
    page_size: int,
    globally_seen_ids: set[str],
) -> tuple[pd.DataFrame, int]:
    """Fetch a proportional sample for one month, using fallback pages."""

    month = item["month"]
    source_count = int(item["source_count"])
    quota = int(item["sample_target"])

    if quota == 0:
        return pd.DataFrame(columns=FIELDS), 0

    month_end = month + pd.offsets.MonthBegin(1)
    start_text = month.strftime("%Y-%m-%dT%H:%M:%S")
    end_text = month_end.strftime("%Y-%m-%dT%H:%M:%S")

    selected_query = (
        f"SELECT {', '.join(FIELDS)} "
        f"WHERE created_date >= '{start_text}' "
        f"AND created_date < '{end_text}' "
        "ORDER BY created_date ASC, unique_key ASC"
    )

    total_pages = math.ceil(source_count / page_size)
    initial_pages_needed = min(
        total_pages,
        max(1, math.ceil(quota / page_size)),
    )

    initial_pages = select_page_numbers(
        total_pages,
        initial_pages_needed,
    )
    initial_page_set = set(initial_pages)

    # If an API page is shorter than the requested page size, try additional
    # pages. The fallback list is only used until the monthly quota is met.
    fallback_pages = [
        page for page in range(1, total_pages + 1)
        if page not in initial_page_set
    ]
    pages_to_try = initial_pages + fallback_pages

    print(
        f"\nMonth: {month:%Y-%m} | "
        f"Source: {source_count:,} | "
        f"Quota: {quota:,} | "
        f"Initial pages: {len(initial_pages):,}"
    )

    page_frames: list[pd.DataFrame] = []
    local_seen_ids: set[str] = set()
    collected_count = 0
    pages_fetched = 0

    for page_number in pages_to_try:
        page_df = fetch_month_page(
            session=session,
            query=selected_query,
            page_number=page_number,
            page_size=page_size,
        )
        pages_fetched += 1

        if not page_df.empty:
            ids = page_df["unique_key"].astype("string").str.strip()
            page_df["unique_key"] = ids

            valid_id_mask = ids.notna() & ids.ne("")
            page_df = page_df.loc[valid_id_mask].copy()

            # Exclude duplicate IDs within this month and IDs already saved
            # from earlier months.
            page_ids = page_df["unique_key"].astype(str)
            new_id_mask = ~page_ids.isin(local_seen_ids)
            new_id_mask &= ~page_ids.isin(globally_seen_ids)
            page_df = page_df.loc[new_id_mask].copy()

            new_ids = page_df["unique_key"].astype(str).tolist()
            local_seen_ids.update(new_ids)

            if not page_df.empty:
                page_frames.append(page_df)
                collected_count += len(page_df)

        print(
            f"  Page {page_number}: "
            f"{len(page_df):,} new unique records | "
            f"Collected: {collected_count:,}/{quota:,}"
        )

        if collected_count >= quota:
            break

        time.sleep(REQUEST_PAUSE_SECONDS)

    if collected_count < quota:
        raise RuntimeError(
            f"Could not meet the monthly quota for {month:%Y-%m}. "
            f"Required {quota:,}; collected {collected_count:,} after "
            f"trying {pages_fetched:,} pages out of {total_pages:,}. "
            "The partial extraction will not be published as a completed file."
        )

    month_df = pd.concat(page_frames, ignore_index=True)
    month_df = month_df.drop_duplicates(subset=["unique_key"], keep="first")

    # Keep exactly the allocated monthly quota.
    month_df = month_df.head(quota).copy()

    if len(month_df) != quota:
        raise RuntimeError(
            f"Monthly quota validation failed for {month:%Y-%m}: "
            f"expected {quota:,}, got {len(month_df):,}."
        )

    globally_seen_ids.update(month_df["unique_key"].astype(str).tolist())

    print(
        f"  Monthly final: {len(month_df):,}/{quota:,} records "
        f"({pages_fetched:,} pages fetched)"
    )

    return month_df, pages_fetched


# ---------------------------------------------------------------------
# 8. MAIN EXTRACTION WORKFLOW
# ---------------------------------------------------------------------

def main() -> None:
    if not APP_TOKEN or APP_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise ValueError(
            "NYC_APP_TOKEN is missing. Add your application token to the "
            ".env file in the UrbanPulse project root. Do not share the token."
        )

    if TARGET_RECORDS <= 0:
        raise ValueError("TARGET_RECORDS must be greater than zero.")

    if PAGE_SIZE <= 0:
        raise ValueError("PAGE_SIZE must be greater than zero.")

    # Keep page sizes moderate and within the API's supported request range.
    page_size = min(PAGE_SIZE, 5000)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_file = RAW_DIR / f"urban_pulse_nyc311_raw_{timestamp}.csv"
    temporary_file = RAW_DIR / f"urban_pulse_nyc311_raw_{timestamp}.csv.part"
    metadata_file = RAW_DIR / f"extraction_metadata_{timestamp}.json"
    monthly_report_file = (
        REPORT_DIR / f"nyc311_monthly_source_counts_{timestamp}.csv"
    )

    print("=" * 70)
    print("URBAN PULSE - HISTORICAL DATA EXTRACTION")
    print("=" * 70)
    print(f"Dataset ID: {DATASET_ID}")
    print(f"Requested target: {TARGET_RECORDS:,}")
    print(f"Page size: {page_size:,}")
    print(f"Start date: {START_DATE}")
    print("Sampling: proportional monthly allocation with spread pages")

    session = create_session()
    globally_seen_ids: set[str] = set()
    monthly_report: list[dict[str, Any]] = []
    extracted_total = 0
    first_write = True

    try:
        monthly_data = get_monthly_source_counts(session)
        actual_target = allocate_monthly_quotas(
            monthly_data,
            TARGET_RECORDS,
        )

        if actual_target < TARGET_RECORDS:
            print(
                "WARNING: The available source count is below the requested "
                f"target. Adjusted target: {actual_target:,}."
            )

        print(f"\nMonthly allocation target: {actual_target:,}")

        # Ensure the temporary output starts clean without touching older files.
        if temporary_file.exists():
            temporary_file.unlink()

        for item in monthly_data:
            month = item["month"]
            quota = int(item["sample_target"])

            if quota == 0:
                monthly_report.append(
                    {
                        "month": month.strftime("%Y-%m"),
                        "source_count": int(item["source_count"]),
                        "sample_target": 0,
                        "records_extracted": 0,
                        "pages_fetched": 0,
                    }
                )
                continue

            month_df, pages_fetched = extract_month_sample(
                session=session,
                item=item,
                page_size=page_size,
                globally_seen_ids=globally_seen_ids,
            )

            month_df.to_csv(
                temporary_file,
                mode="a" if not first_write else "w",
                header=first_write,
                index=False,
                encoding="utf-8-sig",
            )
            first_write = False

            month_records = len(month_df)
            extracted_total += month_records

            monthly_report.append(
                {
                    "month": month.strftime("%Y-%m"),
                    "source_count": int(item["source_count"]),
                    "sample_target": quota,
                    "records_extracted": month_records,
                    "pages_fetched": pages_fetched,
                }
            )

            print(
                f"Overall progress: {extracted_total:,}/{actual_target:,}"
            )

        # Final validations before publishing the CSV.
        if extracted_total != actual_target:
            raise RuntimeError(
                "Overall extraction count does not match the allocated target: "
                f"expected {actual_target:,}, got {extracted_total:,}."
            )

        if len(globally_seen_ids) != extracted_total:
            raise RuntimeError(
                "Global unique-ID validation failed: "
                f"records={extracted_total:,}, "
                f"unique IDs={len(globally_seen_ids):,}."
            )

        if not temporary_file.exists():
            raise RuntimeError("No temporary CSV was produced.")

        # Publish the final CSV only after all months pass validation.
        temporary_file.replace(output_file)

        monthly_df = pd.DataFrame(monthly_report)
        monthly_df.to_csv(
            monthly_report_file,
            index=False,
            encoding="utf-8-sig",
        )

        created_dates = pd.to_datetime(
            monthly_df.loc[monthly_df["records_extracted"] > 0, "month"],
            errors="coerce",
        )

        metadata = {
            "project": "Urban Pulse",
            "dataset_id": DATASET_ID,
            "dataset_url": DATASET_URL,
            "api_endpoint": API_CSV_URL,
            "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
            "start_date_filter": START_DATE,
            "requested_target_records": TARGET_RECORDS,
            "allocated_target_records": actual_target,
            "actual_records_extracted": extracted_total,
            "unique_request_ids": len(globally_seen_ids),
            "duplicate_request_ids": 0,
            "months_with_sample": int(
                (monthly_df["records_extracted"] > 0).sum()
            ),
            "page_size": page_size,
            "sampling_method": (
                "Proportional allocation by monthly source counts; "
                "spread SODA3 pages with fallback pages; exact monthly quotas"
            ),
            "selected_fields": FIELDS,
            "raw_file": output_file.name,
            "monthly_source_report": monthly_report_file.name,
            "monthly_results": monthly_report,
            "first_sampled_month": (
                created_dates.min().strftime("%Y-%m")
                if not created_dates.empty else None
            ),
            "last_sampled_month": (
                created_dates.max().strftime("%Y-%m")
                if not created_dates.empty else None
            ),
        }

        with metadata_file.open("w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=4)

        print("\n" + "=" * 70)
        print("HISTORICAL EXTRACTION COMPLETED")
        print("=" * 70)
        print(f"Requested target: {TARGET_RECORDS:,}")
        print(f"Allocated target: {actual_target:,}")
        print(f"Actual records: {extracted_total:,}")
        print(f"Unique request IDs: {len(globally_seen_ids):,}")
        print(
            "Months covered: "
            f"{int((monthly_df['records_extracted'] > 0).sum()):,}"
        )
        print(f"Raw dataset: {output_file}")
        print(f"Monthly source report: {monthly_report_file}")
        print(f"Metadata: {metadata_file}")
        print("Previous raw and cleaned files were not modified.")

    except Exception:
        # Never leave an incomplete .part file that could be mistaken
        # for a completed extraction.
        if temporary_file.exists():
            temporary_file.unlink()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nEXTRACTION FAILED")
        print(f"Reason: {error}")
        sys.exit(1)
