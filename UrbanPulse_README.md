# Urban Pulse - Citizen Complaint Intelligence & Resolution Analytics

GUVI AIML mini-project built with Python, NYC Open Data 311 requests, MySQL and Streamlit.

## Project status

The learner verified the following locally on 15 November 2026:

- 100,000 historical sample records, covering 83 months from 2020-01-01 to 2026-11-14.
- 100,000 unique request IDs; zero duplicate excess and zero missing request IDs.
- 44 cleaned columns (25 source fields plus 19 derived/validation fields).
- 100,000 rows loaded into both MySQL staging and main tables.
- 15 analyst investigations executed in MySQL Workbench.
- Seven Streamlit pages launched and reviewed; date, borough and complaint filters tested.
- Five KPI calculations checked against MySQL; the over-72-hour KPI was verified at 22.03% (21,638 / 98,214).

**Important:** The 100,000 records are a monthly-proportional, distributed-page sample, not a complete extract of all NYC 311 records and not a random sample. Use the saved monthly source-count report when describing full-source monthly volumes. The sample may retain systematic selection bias.

## Data source

NYC Open Data - 311 Service Requests from 2020 to Present

Dataset ID: `erm2-nwe9`

Dataset page: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9

SODA 3 query endpoint used by the extraction program:
`https://data.cityofnewyork.us/api/v3/views/erm2-nwe9/query.json`

CSV query endpoint used by the extraction program:
`https://data.cityofnewyork.us/api/v3/views/erm2-nwe9/query.csv`

## Technology

- Python 3.13.x
- Pandas, NumPy, Requests, Matplotlib, SciPy
- MySQL Server 8.x and MySQL Workbench
- mysql-connector-python
- Streamlit and Plotly
- python-dotenv

## Folder structure

```text
UrbanPulse/
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
├── src/
│   ├── test_nyc_api.py
│   ├── extract_data.py
│   ├── clean_data.py
│   ├── analysis.py
│   └── load_data.py
├── sql/
│   ├── create_database.sql
│   ├── analyst_queries_01_15.sql
│   ├── analyst_queries_09_15_reference.sql
│   └── dashboard_validation.sql
├── dashboard/
│   └── app.py
├── data/
│   ├── raw/          # Copy local raw CSV and metadata JSON here
│   └── processed/    # Copy local cleaned CSV here
├── reports/
│   ├── figures/
│   ├── screenshots/
│   ├── UrbanPulse_Final_Project_Report.docx
│   └── UrbanPulse_Data_Dictionary_and_Analysis.xlsx
└── logs/
```

## Windows setup

1. Install Python 3.13.x, VS Code, MySQL Server and MySQL Workbench.
2. Open Command Prompt in the project directory.
3. Create and activate a virtual environment:

```cmd
python -m venv .venv
.venv\Scripts\activate
```

4. Install dependencies:

```cmd
python -m pip install --upgrade pip
pip install -r requirements.txt
```

5. Copy `.env.example` to `.env`, then set your NYC application token and MySQL password. Never share or commit `.env`.
6. In MySQL Workbench, execute `sql/create_database.sql`.

## Pipeline execution

Run from the project root with `.venv` activated.

```cmd
python src\test_nyc_api.py
python src\extract_data.py
python src\clean_data.py
python src\analysis.py
python src\load_data.py
streamlit run dashboard\app.py
```

The extraction script creates timestamped raw CSV and metadata files and a monthly source-count report in `reports/`. The cleaner creates a separate timestamped processed CSV and data-quality reports. The EDA program creates CSV/JSON summaries and charts. The loader validates the staging table before publishing to `service_requests`.

For reproducibility, record the extraction timestamp and keep the matching monthly source-count report with the raw file. The scripts select the newest matching files by modification time; when working with multiple runs, verify the selected filename printed by each script.

## MySQL objects

Database: `urban_pulse_db`

Tables:
- `service_requests_staging`
- `service_requests`

The loader expects the 44-column CSV order documented in the data dictionary. Do not edit column order manually.

## Five dashboard KPIs

1. Total requests: distinct `unique_key` values in the filtered sample.
2. Recorded closure: rows where `is_closed = TRUE` divided by all filtered rows. `is_closed` is derived from the presence of `closed_date`, not text `status`.
3. Open requests: rows where `is_closed = FALSE`.
4. Median resolution: median of non-NULL, non-negative `resolution_hours`.
5. Over 72 hours: valid non-negative durations strictly greater than 72 hours divided by all valid non-negative durations.

Verified full-sample baseline:

| KPI | Value |
|---|---:|
| Total requests | 100,000 |
| Recorded closure | 98.43% (98,429 / 100,000) |
| Open requests | 1,571 |
| Median resolution | 3.2152 hours (displayed 3.22 h) |
| Over 72 hours | 22.03% (21,638 / 98,214) |

The 72-hour threshold is illustrative and is not presented as an official NYC service-level standard.

## Interpretation and limitations

- NYC 311 records are reported service requests, not a census of all urban problems.
- The source data changes over time; results depend on the extraction date and selected sample.
- The 100,000-row sample uses monthly proportional allocation and distributed pages; it is not a random sample and can have selection bias.
- November 2026 is partial through November 14, 2026.
- A recorded closure does not measure citizen satisfaction or independently verified resolution.
- Text `status` and `closed_date` are not identical definitions. The sample has 98,063 literal `Closed` statuses but 98,429 rows with a recorded `closed_date`.
- 215 records had `closed_date` earlier than `created_date`; these are flagged and excluded from valid resolution-duration calculations.
- 98,214 records have valid non-negative resolution durations.
- Python and SQL IQR outlier counts differ by one (15,986 vs 15,985) because the quartile methods differ. State the method when quoting an outlier count.
- Resolution durations are strongly right-skewed (population skewness 10.6499); medians are useful alongside means. Small group counts should be shown with category/agency medians.
- ZIP codes and coordinates can be missing or incomplete; global coordinate-range checks do not prove a point is located within NYC.

## Security and submission

- Do not include `.env`, API tokens, database passwords or local credentials in the submission.
- The raw and processed CSVs are intentionally not bundled here. Copy the local files that were used for your final run into `data/raw/` and `data/processed/` only if your GUVI submission rules permit the data size and redistribution.
- Include the corresponding extraction metadata and monthly source-count report with the sample if allowed.
- Verify that screenshots and report figures reflect the same full date range and the same dataset version used for the final evaluation.
