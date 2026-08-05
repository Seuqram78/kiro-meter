"""Read-only access to Kiro CLI's local session files and SQLite database."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs

from kiro_meter.models import ConversationRow, DbSnapshot, UsageRow

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import date, tzinfo

# Still holds the kiro-cli auth token (``auth_kv``); conversation history has
# moved to per-session files under DEFAULT_SESSIONS_DIR (see load_conversations).
# kiro-cli itself picks this location per-OS (e.g. %LOCALAPPDATA%\kiro-cli on
# Windows, ~/.local/share/kiro-cli on Linux), so we rely on the same
# platformdirs convention rather than hardcoding one OS's layout.
DEFAULT_DB_PATH: Path = (
    platformdirs.user_data_path("kiro-cli", appauthor=False, roaming=False)
    / "data.sqlite3"
)
DEFAULT_SESSIONS_DIR: Path = Path.home() / ".kiro/sessions/cli"

_MS_PER_SECOND = 1000
_MS_PER_MINUTE = 60_000
_CREDIT_UNIT = "credit"
_DEFAULT_BURN_WINDOW_MIN = 15
_DEFAULT_TOP_N = 5
_SUBSECOND = re.compile(r"(\.\d{6})\d+")
_MIN_NESTING = 1

FULL_NESTING = 10_000
"""Sentinel nesting level: any real path saturates to itself well below this."""

MERGED_MODEL = ""
"""Sentinel model id marking a row whose per-model rows were merged together.

Safe as the empty string because ``_parse_session`` falls back to
``"unknown"`` for a missing or empty model id, so no real usage row can ever
carry an empty model (pinned by a test).
"""


def load_conversations(sessions_dir: Path) -> list[ConversationRow]:
    """Load one ConversationRow for each Kiro CLI session file.

    Args:
        sessions_dir: Path to Kiro CLI's ``sessions/cli`` directory, where
            each conversation is stored as a ``{session_id}.json`` file.

    Returns:
        The parsed conversation rows (empty if the directory has no session
        files). Files that fail to parse or don't match the expected shape
        (kiro-cli's session format is undocumented and can drift, and files
        are not written atomically so may be read mid-write) are skipped
        rather than raising.
    """
    if not sessions_dir.is_dir():
        return []
    rows = []
    for path in sessions_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            row = _parse_session(data)
        except OSError, json.JSONDecodeError, AttributeError, TypeError:
            continue
        if row is not None:
            rows.append(row)
    return rows


def _parse_session(data: dict[str, object]) -> ConversationRow | None:
    """Turn one session JSON document into a ConversationRow."""
    session_id = data.get("session_id")
    updated_at = data.get("updated_at")
    if not isinstance(session_id, str) or not isinstance(updated_at, str):
        return None
    state = data.get("session_state") or {}
    metadata = state.get("conversation_metadata") or {}
    turns = metadata.get("user_turn_metadatas") or []
    credit_total = sum(
        usage.get("value", 0.0)
        for turn in turns
        for usage in turn.get("metering_usage") or []
        if usage.get("unit") == _CREDIT_UNIT
    )
    rts_model_state = state.get("rts_model_state") or {}
    model_info = rts_model_state.get("model_info") or {}
    model_id = model_info.get("model_id")
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
        ``by_folder_model`` groups by the exact folder path; use
        ``collapse_by_nesting`` to re-group it to a coarser depth.
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
        by_folder_model=_by_folder_model(usage_rows),
        recent=tuple(ordered[:_DEFAULT_TOP_N]),
        approx=True,
    )


def _local_date(updated_at_ms: int, tz: tzinfo) -> date:
    """Return the local calendar date of a millisecond timestamp."""
    return datetime.fromtimestamp(updated_at_ms / _MS_PER_SECOND, tz=tz).date()


def _aggregate(
    rows: Iterable[UsageRow],
    key: Callable[[UsageRow], tuple[str, str]],
) -> tuple[UsageRow, ...]:
    """Group usage rows by ``key``, summing turns and credits, ranked descending.

    The one grouping implementation behind every breakdown the app offers
    (per folder+model, collapsed to a nesting depth, and models merged) - they
    differ only in the key they group on. Ties keep the order their group was
    first seen in, since ``sorted`` is stable over an insertion-ordered dict.
    """
    turns: dict[tuple[str, str], int] = {}
    totals: dict[tuple[str, str], float] = {}
    for row in rows:
        group = key(row)
        turns[group] = turns.get(group, 0) + row.turns
        totals[group] = totals.get(group, 0.0) + row.credits
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return tuple(
        UsageRow(folder, model, turns[folder, model], total)
        for (folder, model), total in ranked
    )


def _by_folder_model(rows: list[ConversationRow]) -> tuple[UsageRow, ...]:
    """Group credits and turn counts by (folder, model), all groups descending."""
    return _aggregate(
        (
            UsageRow(row.folder, row.model_id or "unknown", 1, row.credits)
            for row in rows
        ),
        key=lambda row: (row.folder, row.model),
    )


def merge_models(by_folder_model: tuple[UsageRow, ...]) -> tuple[UsageRow, ...]:
    """Merge each folder's per-model rows into one row per folder.

    Merged rows carry ``MERGED_MODEL`` in place of a model id. Apply this
    *after* ``collapse_by_nesting`` when both are active, so models are merged
    within folders that are already grouped, never across folders that haven't
    been grouped together yet.
    """
    return _aggregate(by_folder_model, key=lambda row: (row.folder, MERGED_MODEL))


def collapse_by_nesting(
    by_folder_model: tuple[UsageRow, ...],
    nesting: int,
) -> tuple[UsageRow, ...]:
    """Re-group an already-aggregated folder/model breakdown to a coarser depth.

    Collapses each folder to its leading ``nesting`` path segments, root
    anchored (e.g. ``/home/me/proj-a/sub`` at nesting 1/2/3 becomes
    ``/home``, ``/home/me``, ``/home/me/proj-a`` - a depth at or beyond the
    path's real segment count returns it unchanged). This walks the same
    tree everyone's paths share from its root, so folders under a common
    ancestor collapse into that ancestor rather than merging on a
    coincidentally-matching leaf name. Distinct folders that collapse to the
    same key have their turns/credits summed; distinct models at the same
    collapsed folder remain separate rows.
    """
    return _aggregate(
        by_folder_model,
        key=lambda row: (_collapse_folder(row.folder, nesting), row.model),
    )


def _path_names(folder: str) -> tuple[str, ...]:
    """The real path segments of ``folder``, excluding any root/drive anchor.

    Uses ``pathlib.Path`` rather than a hardcoded ``/`` split so this parses
    Windows paths (drive-letter anchor, backslash separators) the same way
    it parses POSIX ones - conversation folders are read from the local
    machine, so they're always in that machine's own path convention.
    """
    path = Path(folder)
    return path.parts[1:] if path.anchor else path.parts


def _collapse_folder(folder: str, nesting: int) -> str:
    """Collapse a folder path to its leading ``nesting`` segments, root anchored.

    E.g. ``/home/me/proj-a/sub`` at nesting 1/2/3 becomes ``/home``,
    ``/home/me``, ``/home/me/proj-a`` (and likewise for a Windows path's
    drive letter). The root/drive anchor doesn't count against the nesting
    depth itself - it's kept on every level so a collapsed folder is still a
    real, absolute path prefix. A depth at or beyond the path's real segment
    count returns the path unchanged.
    """
    path = Path(folder)
    parts = path.parts
    offset = len(parts) - len(_path_names(folder))
    depth = max(_MIN_NESTING, nesting)  # nesting=0 would otherwise mean "all"
    names = parts[offset:]
    if not names or depth >= len(names):
        return folder
    return str(Path(*parts[: offset + depth]))


def max_folder_depth(rows: list[ConversationRow]) -> int:
    """Return the deepest folder path (in real segments, anchor excluded) among rows.

    Returns 1 if there are no rows, so callers can always clamp a nesting
    level to at least this value without a special-case for "empty".
    """
    if not rows:
        return _MIN_NESTING
    return max((len(_path_names(r.folder)) or _MIN_NESTING) for r in rows)
