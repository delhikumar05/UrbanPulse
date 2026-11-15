import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# Find the main project folder
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load API token
load_dotenv(PROJECT_ROOT / ".env")

APP_TOKEN = os.getenv("NYC_APP_TOKEN", "").strip()

API_URL = (
    "https://data.cityofnewyork.us/"
    "api/v3/views/erm2-nwe9/query.json"
)


def test_api_connection():

    if not APP_TOKEN:
        print("ERROR: NYC application token not found.")
        return

    headers = {
        "X-App-Token": APP_TOKEN,
        "Content-Type": "application/json"
    }

    payload = {
        "query": (
            "SELECT unique_key, created_date, "
            "complaint_type, borough "
            "ORDER BY created_date DESC "
            "LIMIT 5"
        ),
        "includeSynthetic": False
    }

    try:
        print("Connecting to NYC Open Data API...")

        response = requests.post(
            API_URL,
            headers=headers,
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        records = response.json()

        print("\nAPI CONNECTION SUCCESSFUL!")
        print("Records retrieved:", len(records))

        for record in records:
            print(
                record.get("unique_key"),
                "|",
                record.get("complaint_type"),
                "|",
                record.get("borough")
            )

    except requests.exceptions.HTTPError as error:
        print("API HTTP ERROR:", error)
        print("Response:", response.text[:1000])

    except requests.exceptions.RequestException as error:
        print("CONNECTION ERROR:", error)

    except ValueError as error:
        print("INVALID API RESPONSE:", error)


if __name__ == "__main__":
    test_api_connection()