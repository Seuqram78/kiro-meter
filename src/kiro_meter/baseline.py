"""Persist a per-day baseline of official API usage for the "today" figure."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import platformdirs

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

DEFAULT_STATE_DB_PATH: Path = (
    platformdirs.user_data_path("kiro-meter", appauthor=False, roaming=False)
    / "state.sqlite3"
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS day_baseline (
    day TEXT PRIMARY KEY,
    used REAL NOT NULL
)
"""
_UPSERT = """
INSERT INTO day_baseline (day, used) VALUES (?, ?)
ON CONFLICT(day) DO UPDATE SET used = excluded.used
WHERE excluded.used < day_baseline.used
"""
_SELECT = "SELECT used FROM day_baseline WHERE day = ?"


def resolve_daily_baseline(db_path: Path, today: date, used: float) -> float:
    """Return today's baseline API usage, capturing it on the first call of the day.

    Subsequent calls the same day return the originally captured value, so
    "today usage" is `used - baseline` from then on. If a later call observes
    a lower `used` than the stored baseline (the billing cycle reset partway
    through the local day), the baseline is lowered to match, so today's
    usage resumes counting from zero instead of going negative.
    """
    day = today.isoformat()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
        conn.execute(_UPSERT, (day, used))
        conn.commit()
        row = conn.execute(_SELECT, (day,)).fetchone()
    finally:
        conn.close()
    return row[0]
