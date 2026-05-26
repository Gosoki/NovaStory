from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "experiment_results.csv"

COLUMNS = [
    "User_ID",
    "Topic",
    "Group",
    "Total_Time_Seconds",
    "Initial_Input",
    "Interventions",
    "Final_Output",
    "Timestamp",
]


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CSV_PATH.exists():
        with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(COLUMNS)


def append_row(
    *,
    user_id: str,
    topic: dict,
    group: str,
    total_time_seconds: float,
    initial_input: str,
    final_output: str,
    interventions: Optional[dict] = None,
) -> None:
    _ensure_file()
    row = [
        user_id,
        json.dumps(topic, ensure_ascii=False),
        group,
        f"{total_time_seconds:.2f}",
        initial_input or "",
        json.dumps(interventions, ensure_ascii=False) if interventions else "",
        final_output or "",
        datetime.now().isoformat(timespec="seconds"),
    ]
    with CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(row)


def load_df() -> pd.DataFrame:
    if not CSV_PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        return pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=COLUMNS)


def download_bytes() -> bytes:
    if not CSV_PATH.exists():
        return b""
    return CSV_PATH.read_bytes()
