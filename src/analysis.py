"""
URBAN PULSE - Exploratory Data Analysis (EDA)

Reads the newest cleaned NYC 311 dataset and creates:
- Descriptive statistics for valid resolution durations
- IQR outlier summary and flagged outlier records
- Complaint, agency, borough and status summaries
- Monthly sample counts and (when available) full source monthly counts
- Weekday and hour summaries
- EDA charts in reports/figures/

Important:
The 100,000 records are a sample. Monthly source_count values from the
extraction report represent the full source monthly totals. Do not treat
sample counts as complete citywide monthly totals.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ------------------------------------------------------------
# 1. PATHS AND CONFIGURATION
# ------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
REPORT_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORT_DIR / "figures"

REPORT_DIR.mkdir(parents=True, exist_ok=True)
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

MIN_GROUP_SIZE = 5  # suppress unstable medians for tiny groups


# ------------------------------------------------------------
# 2. FIND INPUT FILES
# ------------------------------------------------------------

def newest_file(pattern: str, folder: Path) -> Path:
    files = list(folder.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"No files matching {pattern!r} were found in {folder}"
        )
    return max(files, key=lambda p: p.stat().st_mtime)


def load_cleaned_data() -> tuple[pd.DataFrame, Path]:
    path = newest_file("urban_pulse_nyc311_cleaned_*.csv", PROCESSED_DIR)
    print(f"Loading cleaned dataset: {path.name}")

    df = pd.read_csv(
        path,
        dtype={"unique_key": "string", "incident_zip": "string"},
        low_memory=False,
    )

    required = {
        "unique_key", "created_date", "closed_date", "agency",
        "complaint_type", "borough", "status", "latitude",
        "longitude", "resolution_hours", "is_closed",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Cleaned dataset is missing columns: {sorted(missing)}")

    return df, path


def load_monthly_source_counts() -> tuple[pd.DataFrame | None, Path | None]:
    files = list(REPORT_DIR.glob("nyc311_monthly_source_counts_*.csv"))
    if not files:
        return None, None

    path = max(files, key=lambda p: p.stat().st_mtime)
    monthly = pd.read_csv(path)
    required = {"month", "source_count"}
    if not required.issubset(monthly.columns):
        print(
            "WARNING: Latest monthly source-count report does not contain "
            "the expected month/source_count columns."
        )
        return None, path

    monthly["month"] = pd.PeriodIndex(monthly["month"].astype(str), freq="M")
    monthly["source_count"] = pd.to_numeric(
        monthly["source_count"], errors="coerce"
    )
    return monthly, path


# ------------------------------------------------------------
# 3. PREPARE ANALYSIS FIELDS
# ------------------------------------------------------------

def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["created_date"] = pd.to_datetime(df["created_date"], errors="coerce")
    df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce")
    df["resolution_hours"] = pd.to_numeric(
        df["resolution_hours"], errors="coerce"
    )

    # Derive temporal fields from the parsed creation timestamp.
    df["created_month_period"] = df["created_date"].dt.to_period("M")
    df["created_weekday"] = df["created_date"].dt.day_name()
    df["created_hour"] = df["created_date"].dt.hour

    # Use only finite, non-negative durations for duration statistics.
    duration = df["resolution_hours"].replace([np.inf, -np.inf], np.nan)
    df["valid_resolution_hours"] = duration.where(duration >= 0)

    # Null/blank categories are grouped explicitly as Unknown.
    for column in ["complaint_type", "agency", "borough", "status"]:
        df[column] = df[column].astype("string").str.strip()
        df[column] = df[column].replace("", pd.NA).fillna("Unknown")

    return df


# ------------------------------------------------------------
# 4. DESCRIPTIVE STATISTICS AND OUTLIERS
# ------------------------------------------------------------

def analyze_resolution(df: pd.DataFrame) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    values = df["valid_resolution_hours"].dropna()
    if values.empty:
        raise ValueError("No valid non-negative resolution durations were found.")

    q1 = float(values.quantile(0.25))
    median = float(values.median())
    q3 = float(values.quantile(0.75))
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    modes = values.mode()
    mode_value = float(modes.iloc[0]) if not modes.empty else None

    stats = {
        "measure": [
            "count", "mean", "median", "mode", "minimum", "maximum",
            "range", "q1", "q3", "iqr", "variance", "standard_deviation",
            "skewness", "kurtosis", "lower_iqr_fence", "upper_iqr_fence",
        ],
        "value": [
            int(values.count()),
            float(values.mean()),
            median,
            mode_value,
            float(values.min()),
            float(values.max()),
            float(values.max() - values.min()),
            q1,
            q3,
            iqr,
            float(values.var(ddof=1)),
            float(values.std(ddof=1)),
            float(values.skew()),
            float(values.kurtosis()),
            lower,
            upper,
        ],
    }
    stats_df = pd.DataFrame(stats)

    outlier_mask = (values < lower) | (values > upper)
    outlier_index = values.index[outlier_mask]
    wanted = [
        col for col in [
            "unique_key", "created_date", "complaint_type", "agency",
            "borough", "resolution_hours"
        ] if col in df.columns
    ]
    outliers = df.loc[outlier_index, wanted].copy()
    outliers["outlier_direction"] = np.where(
        outliers["resolution_hours"] < lower, "Below lower fence", "Above upper fence"
    )

    outlier_summary = {
        "valid_duration_count": int(values.count()),
        "q1_hours": q1,
        "median_hours": median,
        "q3_hours": q3,
        "iqr_hours": iqr,
        "lower_fence_hours": lower,
        "upper_fence_hours": upper,
        "outlier_count": int(outlier_mask.sum()),
        "outlier_percentage": float(outlier_mask.mean() * 100),
        "outliers_below_lower_fence": int((values < lower).sum()),
        "outliers_above_upper_fence": int((values > upper).sum()),
        "note": "IQR flags are investigation prompts; outliers are not deleted.",
    }

    return stats_df, outlier_summary, outliers


# ------------------------------------------------------------
# 5. GROUPED ANALYSIS TABLES
# ------------------------------------------------------------

def grouped_resolution_median(
    df: pd.DataFrame, group_column: str
) -> pd.DataFrame:
    result = (
        df.dropna(subset=["valid_resolution_hours"])
        .groupby(group_column, dropna=False)["valid_resolution_hours"]
        .agg(record_count="count", median_resolution_hours="median")
        .reset_index()
    )
    result = result[result["record_count"] >= MIN_GROUP_SIZE]
    return result.sort_values(
        ["median_resolution_hours", "record_count"], ascending=[False, False]
    )


def create_summary_tables(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}

    tables["top_complaints"] = (
        df.groupby("complaint_type").size().rename("request_count")
        .reset_index().sort_values("request_count", ascending=False).head(20)
    )

    tables["agency_workload"] = (
        df.groupby("agency").size().rename("request_count")
        .reset_index().sort_values("request_count", ascending=False)
    )

    tables["borough_workload"] = (
        df.groupby("borough").size().rename("request_count")
        .reset_index().sort_values("request_count", ascending=False)
    )

    tables["status_distribution"] = (
        df.groupby("status").size().rename("request_count")
        .reset_index().sort_values("request_count", ascending=False)
    )

    weekday_order = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday"
    ]
    weekday = df.groupby("created_weekday").size().rename("request_count")
    tables["weekday_requests"] = (
        weekday.reindex(weekday_order, fill_value=0)
        .rename_axis("weekday").reset_index()
    )

    hour = df.groupby("created_hour").size().rename("request_count")
    tables["hourly_requests"] = (
        hour.reindex(range(24), fill_value=0)
        .rename_axis("hour").reset_index()
    )

    monthly_sample = (
        df.dropna(subset=["created_month_period"])
        .groupby("created_month_period").size()
        .rename("sample_records").reset_index()
    )
    monthly_sample["month"] = monthly_sample["created_month_period"].astype(str)
    tables["monthly_sample_requests"] = monthly_sample[["month", "sample_records"]]

    tables["median_resolution_by_complaint"] = grouped_resolution_median(
        df, "complaint_type"
    )
    tables["median_resolution_by_agency"] = grouped_resolution_median(
        df, "agency"
    )

    # High volume + long median duration categories, using sample records.
    complaint_resolution = grouped_resolution_median(df, "complaint_type")
    complaint_volume = tables["top_complaints"].rename(
        columns={"request_count": "sample_request_count"}
    )
    combined = complaint_volume.merge(
        complaint_resolution[["complaint_type", "record_count", "median_resolution_hours"]],
        on="complaint_type",
        how="left",
    )
    tables["high_volume_long_duration"] = combined.sort_values(
        ["sample_request_count", "median_resolution_hours"],
        ascending=[False, False],
    )

    return tables


# ------------------------------------------------------------
# 6. MONTHLY SOURCE COUNTS VS SAMPLE COUNTS
# ------------------------------------------------------------

def build_monthly_comparison(
    df: pd.DataFrame, source_monthly: pd.DataFrame | None
) -> pd.DataFrame:
    sample = (
        df.dropna(subset=["created_month_period"])
        .groupby("created_month_period").size()
        .rename("sample_records").reset_index()
    )
    sample["month"] = sample["created_month_period"].astype(str)
    sample = sample[["month", "sample_records"]]

    if source_monthly is None:
        sample["source_count"] = pd.NA
        sample["sample_target"] = pd.NA
        return sample

    source = source_monthly.copy()
    source["month"] = source["month"].astype(str)
    keep = ["month", "source_count"]
    if "sample_target" in source.columns:
        keep.append("sample_target")
    if "records_extracted" in source.columns:
        keep.append("records_extracted")

    return source[keep].merge(sample, on="month", how="outer").sort_values("month")


# ------------------------------------------------------------
# 7. CHARTS
# ------------------------------------------------------------

def save_bar_chart(
    data: pd.DataFrame,
    x: str,
    y: str,
    title: str,
    xlabel: str,
    ylabel: str,
    filename: str,
    rotate: int = 35,
    horizontal: bool = False,
) -> None:
    fig, ax = plt.subplots(figsize=(11, 6))
    if horizontal:
        plot_data = data.sort_values(y, ascending=True)
        ax.barh(plot_data[x].astype(str), plot_data[y])
    else:
        ax.bar(data[x].astype(str), data[y])
        ax.tick_params(axis="x", labelrotation=rotate)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / filename, dpi=160)
    plt.close(fig)


def create_charts(
    df: pd.DataFrame,
    tables: dict[str, pd.DataFrame],
    monthly: pd.DataFrame,
) -> None:
    # Histogram: display is capped at the sample 99th percentile only to
    # make the central distribution readable; calculations use all values.
    durations = df["valid_resolution_hours"].dropna()
    display_cap = float(durations.quantile(0.99))
    display_values = durations[durations <= display_cap]

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.hist(display_values, bins=50)
    ax.set_title("Resolution Hours Distribution (display through 99th percentile)")
    ax.set_xlabel("Resolution hours")
    ax.set_ylabel("Number of requests")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "resolution_hours_histogram.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.boxplot(
    durations,
    orientation="horizontal",
    showfliers=False
)
    ax.set_title("Resolution Hours Boxplot (outlier points hidden for readability)")
    ax.set_xlabel("Resolution hours")
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "resolution_hours_boxplot.png", dpi=160)
    plt.close(fig)

    save_bar_chart(
        tables["top_complaints"].head(15), "complaint_type", "request_count",
        "Top 15 Complaint Types in the 100,000-Record Sample",
        "Complaint type", "Sample request count", "top_complaint_types.png",
        rotate=55,
    )
    save_bar_chart(
        tables["agency_workload"].head(15), "agency", "request_count",
        "Top 15 Agencies by Sample Workload", "Agency", "Sample request count",
        "agency_workload.png", rotate=0,
    )
    save_bar_chart(
        tables["borough_workload"], "borough", "request_count",
        "Requests by Borough in the Sample", "Borough", "Sample request count",
        "borough_workload.png", rotate=25,
    )
    save_bar_chart(
        tables["weekday_requests"], "weekday", "request_count",
        "Requests by Weekday in the Sample", "Weekday", "Sample request count",
        "weekday_requests.png", rotate=25,
    )
    save_bar_chart(
        tables["hourly_requests"], "hour", "request_count",
        "Requests by Hour of Day in the Sample", "Hour (0-23)",
        "Sample request count", "hourly_requests.png", rotate=0,
    )

    if not monthly.empty:
        plot = monthly.copy()
        plot["month"] = pd.PeriodIndex(plot["month"].astype(str), freq="M").to_timestamp()
        fig, ax = plt.subplots(figsize=(13, 6))
        if "source_count" in plot.columns and plot["source_count"].notna().any():
            ax.plot(plot["month"], plot["source_count"], label="Full source monthly count")
            ax.set_ylabel("Full source request count")
            ax.set_title("NYC 311 Monthly Source Volume (Full Source Counts)")
        else:
            ax.plot(plot["month"], plot["sample_records"], label="Sample records")
            ax.set_ylabel("Sample request count")
            ax.set_title("Monthly Requests in the 100,000-Record Sample")
        ax.set_xlabel("Month")
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "monthly_request_trend.png", dpi=160)
        plt.close(fig)


# ------------------------------------------------------------
# 8. MAIN
# ------------------------------------------------------------

def main() -> None:
    print("=" * 65)
    print("URBAN PULSE - EXPLORATORY DATA ANALYSIS")
    print("=" * 65)

    df, input_path = load_cleaned_data()
    df = prepare_data(df)
    source_monthly, source_path = load_monthly_source_counts()

    if source_path:
        print(f"Monthly source-count report: {source_path.name}")
    else:
        print("WARNING: No monthly source-count report found; monthly chart will show sample counts only.")

    stats_df, outlier_summary, outliers = analyze_resolution(df)
    tables = create_summary_tables(df)
    monthly_comparison = build_monthly_comparison(df, source_monthly)

    # Save tables and summaries.
    stats_df.to_csv(REPORT_DIR / "eda_resolution_descriptive_statistics.csv", index=False)
    outliers.to_csv(REPORT_DIR / "eda_resolution_outliers.csv", index=False)
    monthly_comparison.to_csv(REPORT_DIR / "eda_monthly_source_vs_sample.csv", index=False)

    for name, table in tables.items():
        table.to_csv(REPORT_DIR / f"eda_{name}.csv", index=False)

    parsed_dates = df["created_date"].dropna()
    closure_count = int(df["is_closed"].sum()) if "is_closed" in df else int(df["closed_date"].notna().sum())

    summary = {
        "project": "Urban Pulse",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_file": input_path.name,
        "records_analyzed": int(len(df)),
        "unique_request_ids": int(df["unique_key"].nunique()),
        "duplicate_request_id_excess": int(df["unique_key"].notna().sum() - df["unique_key"].nunique()),
        "earliest_created_date": parsed_dates.min().isoformat() if not parsed_dates.empty else None,
        "latest_created_date": parsed_dates.max().isoformat() if not parsed_dates.empty else None,
        "months_covered_in_sample": int(df["created_month_period"].nunique()),
        "recorded_closed_requests": closure_count,
        "recorded_open_requests": int(len(df) - closure_count),
        "valid_resolution_durations": int(df["valid_resolution_hours"].notna().sum()),
        "resolution_outliers": outlier_summary,
        "monthly_source_count_file": source_path.name if source_path else None,
        "interpretation_note": (
            "The cleaned 100,000 records are a sample. Use monthly source_count "
            "from the extraction report for full-source monthly volume; use sample "
            "records for sample-based comparisons. IQR outliers are flagged, not removed."
        ),
    }

    with open(REPORT_DIR / "eda_summary.json", "w", encoding="utf-8") as file:
        json.dump(summary, file, indent=4)

    create_charts(df, tables, monthly_comparison)

    print("\nEDA COMPLETED")
    print("-" * 65)
    print("Records analyzed:", f"{len(df):,}")
    print("Months in sample:", df["created_month_period"].nunique())
    print("Valid resolution durations:", f"{df['valid_resolution_hours'].notna().sum():,}")
    print("Resolution outliers:", f"{outlier_summary['outlier_count']:,}")
    print("Resolution median (hours):", round(outlier_summary["median_hours"], 2))
    print("Resolution mean (hours):", round(float(df["valid_resolution_hours"].mean()), 2))
    print("Reports folder:", REPORT_DIR)
    print("Charts folder:", FIGURES_DIR)
    print("\nOutliers have been flagged, not deleted.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\nEDA FAILED:", exc)
        sys.exit(1)