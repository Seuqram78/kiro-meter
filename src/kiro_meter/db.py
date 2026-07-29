"""Read-only access to Kiro CLI's local session files and SQLite database."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from kiro_meter.models import ConversationRow, DbSnapshot

if TYPE_CHECKING:
    from datetime import date, tzinfo

# Still holds the kiro-cli auth token (``auth_kv``); conversation history has
# moved to per-session files under DEFAULT_SESSIONS_DIR (see load_conversations).
DEFAULT_DB_PATH: Path = Path.home() / ".local/share/kiro-cli/data.sqlite3"
DEFAULT_SESSIONS_DIR: Path = Path.home() / ".kiro/sessions/cli"

_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60_000
_CREDIT_UNIT = "credit"
_DEFAULT_BURN_WINDOW_MIN = 15
_DEFAULT_TOP_N = 5
_SUBSECOND = re.compile(r"(\.\d{6})\d+")


def load_conversations(sessions_dir: Path) -> list[ConversationRow]:
    """Load one ConversationRow for each Kiro CLI session file.

    Args:
        sessions_dir: Path to Kiro CLI's ``sessions/cli`` directory, where
            each conversation is stored as a ``{session_id}.json`` file.

    Returns:
        The parsed conversation rows (empty if the directory has no session
        files). Files that fail to parse (e.g. mid-write by kiro-cli) are
        skipped rather than raising, since these files are not written
        atomically.
    """
    if not sessions_dir.is_dir():
        return []
    rows = []
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        row = _parse_session(data)
        if row is not None:
            rows.append(row)
    return rows


def _parse_session(data: dict[str, object]) -> ConversationRow | None:
    """Turn one session JSON document into a ConversationRow."""
    session_id = data.get("session_id")
    updated_at = data.get("updated_at")
    if not isinstance(session_id, str) or not isinstance(updated_at, str):
        return None
    state = data.get("session_state", {}) or {}
    turns = state.get("conversation_metadata", {}).get("user_turn_metadatas", [])
    credit_total = sum(
        usage.get("value", 0.0)
        for turn in turns
        for usage in turn.get("metering_usage", [])
        if usage.get("unit") == _CREDIT_UNIT
    )
    model_id = state.get("rts_model_state", {}).get("model_info", {}).get("model_id")
    cwd = data.get("cwd")
    return ConversationRow(
        session_id,
        cwd or session_id,
        model_id,
        float(credit_total),
        _parse_timestamp_ms(updated_at),
    )


def _parse_timestamp_ms(value: str) -> int:
    """Parse an ISO-8601 UTC timestamp (``...Z``) to epoch milliseconds."""
    trimmed = _SUBSECOND.sub(r"\1", value).replace("Z", "+00:00")
    return int(datetime.fromisoformat(trimmed).timestamp() * _MS_PER_SECOND)


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
