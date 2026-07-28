"""Read-only access to the Kiro CLI local SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kiro_usage.models import ConversationRow, DbSnapshot

if TYPE_CHECKING:
    from datetime import date, tzinfo

DEFAULT_DB_PATH: Path = Path.home() / ".local/share/kiro-cli/data.sqlite3"

_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60_000
_CREDIT_UNIT = "credit"
_DEFAULT_BURN_WINDOW_MIN = 15
_DEFAULT_TOP_N = 5


def load_conversations(db_path: Path) -> list[ConversationRow]:
    """Load one ConversationRow for each row in ``conversations_v2``.

    Args:
        db_path: Path to the Kiro CLI ``data.sqlite3`` file.

    Returns:
        The parsed conversation rows (empty if the table is empty).
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cursor = conn.execute(
            "SELECT conversation_id, key, value, updated_at FROM conversations_v2",
        )
        return [
            _parse_row(cid, key, value, updated) for cid, key, value, updated in cursor
        ]
    finally:
        conn.close()


def _parse_row(cid: str, key: str, value: str, updated_at_ms: int) -> ConversationRow:
    """Turn one raw ``conversations_v2`` row into a ConversationRow."""
    data = json.loads(value)
    credit_total = sum(
        item.get("value", 0.0)
        for item in data.get("user_turn_metadata", {}).get("usage_info", [])
        if item.get("unit") == _CREDIT_UNIT
    )
    history = data.get("history") or []
    latest = history[-1] if history else {}
    model_id = latest.get("request_metadata", {}).get("model_id")
    env_state = latest.get("user", {}).get("env_context", {}).get("env_state", {})
    cwd = env_state.get("current_working_directory")
    return ConversationRow(
        cid, cwd or key, model_id, float(credit_total), int(updated_at_ms)
    )


def build_db_snapshot(
    rows: list[ConversationRow],
    *,
    now: datetime,
    tz: tzinfo,
    since: datetime | None = None,
    burn_window_min: int = _DEFAULT_BURN_WINDOW_MIN,
) -> DbSnapshot:
    """Aggregate conversation rows into a DbSnapshot.

    Args:
        rows: All conversation rows from the database.
        now: The current instant (timezone-aware).
        tz: Timezone defining the "today" boundary.
        since: If set, the folder/model usage only counts conversations updated
            at or after this instant (e.g. the billing-cycle start).
        burn_window_min: Window, in minutes, for the burn-rate estimate.

    Returns:
        The aggregated snapshot. ``approx`` is always True because credits are
        stored at turn-metadata level rather than as a per-turn ledger.
    """
    today = now.astimezone(tz).date()
    since_ms = int(since.timestamp() * _MS_PER_SECOND) if since is not None else None
    usage_rows = [r for r in rows if since_ms is None or r.updated_at_ms >= since_ms]
    today_rows = [r for r in rows if _local_date(r.updated_at_ms, tz) == today]
    window_start_ms = (
        int(now.timestamp() * _MS_PER_SECOND) - burn_window_min * _MS_PER_MINUTE
    )
    recent_rows = [r for r in rows if r.updated_at_ms >= window_start_ms]
    burn = (
        sum(r.credits for r in recent_rows) / burn_window_min if recent_rows else None
    )
    session = max(rows, key=lambda r: r.updated_at_ms, default=None)
    ordered = sorted(rows, key=lambda r: r.updated_at_ms, reverse=True)
    return DbSnapshot(
        today_credits=sum(r.credits for r in today_rows),
        today_turns=len(today_rows),
        session_credits=session.credits if session else 0.0,
        session_turns=1 if session else 0,
        burn_rate_per_min=burn,
        by_folder_model=_by_folder_model(usage_rows, n=_DEFAULT_TOP_N),
        recent=tuple(ordered[:_DEFAULT_TOP_N]),
        approx=True,
    )


def _local_date(updated_at_ms: int, tz: tzinfo) -> date:
    """Return the local calendar date of a millisecond timestamp."""
    return datetime.fromtimestamp(updated_at_ms / _MS_PER_SECOND, tz=tz).date()


def _by_folder_model(
    rows: list[ConversationRow],
    *,
    n: int,
) -> tuple[tuple[str, str, int, float], ...]:
    """Group credits and turn counts by (folder, model), top ``n`` descending."""
    turns: dict[tuple[str, str], int] = {}
    totals: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row.folder, row.model_id or "unknown")
        turns[key] = turns.get(key, 0) + 1
        totals[key] = totals.get(key, 0.0) + row.credits
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return tuple(
        (folder, model, turns[folder, model], total)
        for (folder, model), total in ranked[:n]
    )
