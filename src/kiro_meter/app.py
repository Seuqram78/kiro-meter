"""Assemble snapshots and drive the one-shot and live views."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import httpx
import readchar
from rich.console import Console
from rich.live import Live

from kiro_meter.account import (
    NeedsLoginError,
    fetch_account_info,
    load_token,
    token_expired,
)
from kiro_meter.db import (
    build_db_snapshot,
    collapse_by_nesting,
    load_conversations,
    max_folder_depth,
)
from kiro_meter.interaction import LiveState, apply_key, normalize
from kiro_meter.models import Snapshot
from kiro_meter.pace import billing_cycle_start, compute_pace
from kiro_meter.render import LiveFooterState, render_snapshot

try:
    import termios
except ImportError:  # Windows has no termios
    termios = None

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, tzinfo
    from pathlib import Path

    from kiro_meter.models import (
        AccountInfo,
        AccountStatus,
        AppConfig,
        ConversationRow,
        DbSnapshot,
        PaceInfo,
    )
    from kiro_meter.pace import HolidayProvider

_TICK_SECONDS = 0.25
_NEAR_LIMIT_FRACTION = 0.9
_EXIT_OK = 0
_EXIT_NEAR_LIMIT = 10
_EXIT_AT_LIMIT = 11
_EXIT_INDETERMINATE = 20
_EXIT_ERROR = 30
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunContext:
    """Shared dependencies threaded through the snapshot pipeline."""

    db_path: Path
    sessions_dir: Path
    client: httpx.Client
    tz: tzinfo
    holidays: HolidayProvider | None


def resolve_account(
    cfg: AppConfig,
    ctx: RunContext,
    *,
    now: datetime,
) -> tuple[AccountInfo | None, AccountStatus]:
    """Fetch the official account usage, mapping failures to a status."""
    if not cfg.use_account:
        return None, "disabled"
    token = load_token(ctx.db_path)
    if token is None or token_expired(token, now):
        return None, "needs_login"
    try:
        return fetch_account_info(token, client=ctx.client, now=now), "ok"
    except NeedsLoginError:
        return None, "needs_login"
    except httpx.HTTPError:
        return None, "error"


def build_snapshot(
    cfg: AppConfig,
    ctx: RunContext,
    *,
    account: AccountInfo | None,
    account_status: AccountStatus,
    now: datetime,
) -> Snapshot:
    """Read local spend and combine it with the account into a Snapshot."""
    since = billing_cycle_start(account.next_reset) if account is not None else None
    db = build_db_snapshot(
        load_conversations(ctx.sessions_dir), now=now, tz=ctx.tz, since=since
    )
    pace = (
        compute_pace(account, db, cfg, now=now, holidays=ctx.holidays)
        if account is not None
        else None
    )
    return Snapshot(db, account, account_status, pace, now)


def run_once(cfg: AppConfig, ctx: RunContext, *, now: datetime, as_json: bool) -> int:
    """Render (or print as JSON) a single snapshot and return an exit code."""
    account, status = resolve_account(cfg, ctx, now=now)
    snap = build_snapshot(cfg, ctx, account=account, account_status=status, now=now)
    if as_json:
        sys.stdout.write(json.dumps(_as_dict(snap)) + "\n")
    else:
        Console().print(render_snapshot(snap, cfg))
    return _exit_code(snap)


def run_live(
    cfg: AppConfig,
    ctx: RunContext,
    *,
    now_fn: Callable[[], datetime],
) -> None:
    """Animate a spinner each tick; poll spend and the account on one cadence.

    Renders full-height in the terminal's alternate screen buffer, scrollable
    and re-nestable live via the keyboard (see ``kiro_meter.interaction``).
    All data (local disk reads and the official account fetch) refresh together
    every ``cfg.refresh_seconds``, with the timer starting after the fetch
    completes so a slow network call doesn't compress the interval.
    Re-aggregating by the current nesting level happens every tick from the
    cached rows, so nesting changes feel instant.
    """
    account: AccountInfo | None = None
    status: AccountStatus = "disabled"
    rows: list[ConversationRow] = []
    last_refresh = 0.0
    fetched_at: datetime | None = None
    refreshing = False
    frame = 0
    ui = LiveState()
    key_queue: queue.Queue[str] = queue.Queue()
    stop_reading = threading.Event()
    reader = threading.Thread(
        target=_read_keys, args=(key_queue, stop_reading), daemon=True
    )
    fd = sys.stdin.fileno() if sys.stdin.isatty() else None
    saved_termios = _save_termios(fd)
    reader.start()

    try:
        with Live(auto_refresh=False, screen=True) as live:
            try:
                while True:
                    now = now_fn()
                    monotonic = time.monotonic()

                    if _due(last_refresh, monotonic, cfg.refresh_seconds):
                        refreshing = True
                        account, status = resolve_account(cfg, ctx, now=now)
                        rows = load_conversations(ctx.sessions_dir)
                        last_refresh = time.monotonic()
                        fetched_at = now_fn()
                        refreshing = False

                    ui = _drain_keys(key_queue, ui)
                    if ui.quit:
                        break
                    ui = normalize(ui, max_nesting=max_folder_depth(rows))

                    db = _local_snapshot(rows, now, ctx.tz, account, ui.nesting)
                    pace = (
                        compute_pace(account, db, cfg, now=now, holidays=ctx.holidays)
                        if account is not None
                        else None
                    )
                    snap_time = fetched_at if fetched_at is not None else now
                    snap = Snapshot(db, account, status, pace, snap_time)
                    seconds_until_refresh = max(
                        0.0, cfg.refresh_seconds - (time.monotonic() - last_refresh)
                    )
                    live.update(
                        render_snapshot(
                            snap,
                            cfg,
                            frame=frame,
                            ui=ui,
                            live_footer=LiveFooterState(
                                seconds_until_refresh=seconds_until_refresh,
                                refreshing=refreshing,
                            ),
                        ),
                        refresh=True,
                    )
                    frame += 1
                    time.sleep(_TICK_SECONDS)
            except KeyboardInterrupt:
                pass
    finally:
        stop_reading.set()
        _restore_termios(fd, saved_termios)


def _due(last: float, monotonic: float, interval: float) -> bool:
    """Whether a cadence last run at `last` is due again at `monotonic`."""
    return last == 0.0 or monotonic - last >= interval


def _drain_keys(key_queue: queue.Queue[str], ui: LiveState) -> LiveState:
    """Apply every queued keypress to ui; the most recent state wins."""
    while not key_queue.empty():
        ui = apply_key(ui, key_queue.get_nowait())
    return ui


def _local_snapshot(
    rows: list[ConversationRow],
    now: datetime,
    tz: tzinfo,
    account: AccountInfo | None,
    nesting: int,
) -> DbSnapshot:
    """Aggregate cached rows, re-collapsed to `nesting` - no disk I/O involved.

    Kept separate from ``build_snapshot`` (used by the one-shot path) because
    nesting is display-only live-view state: cheap to re-collapse every tick
    straight from the already-aggregated ``by_folder_model``.
    """
    since = billing_cycle_start(account.next_reset) if account is not None else None
    db = build_db_snapshot(rows, now=now, tz=tz, since=since)
    return replace(db, by_folder_model=collapse_by_nesting(db.by_folder_model, nesting))


def _read_keys(key_queue: queue.Queue[str], stop_event: threading.Event) -> None:
    """Background thread target: push keys onto the queue until told to stop.

    ``readchar.readkey()`` raises ``KeyboardInterrupt`` in-thread on Ctrl-C
    rather than returning it as a key (and a raised exception in a
    non-main thread doesn't interrupt the main loop) - catch it here and
    enqueue the same sentinel ``apply_key`` treats as a quit key, so Ctrl-C
    quits cleanly regardless of whether a real SIGINT also reaches the main
    thread.
    """
    while not stop_event.is_set():
        try:
            key_queue.put(readchar.readkey())
        except KeyboardInterrupt:
            key_queue.put(readchar.key.CTRL_C)
            return


def _save_termios(fd: int | None) -> list[object] | None:
    """Snapshot terminal attributes to force-restore on exit, POSIX only.

    ``readchar`` already saves/restores per keypress, but only covers the
    thread that's reading; this closes the gap where the process tears down
    while that background read is in flight.
    """
    if fd is None or termios is None:
        return None
    return termios.tcgetattr(fd)


def _restore_termios(fd: int | None, saved: list[object] | None) -> None:
    """Restore terminal attributes captured by ``_save_termios``, if any."""
    if fd is None or termios is None or saved is None:
        return
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _as_dict(snap: Snapshot) -> dict[str, object]:
    """Flatten a snapshot into a JSON-serialisable dict (see README for schema)."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": snap.generated_at.isoformat(),
        "account_status": snap.account_status,
        "account": _account_dict(snap.account),
        "today": {"credits": snap.db.today_credits, "turns": snap.db.today_turns},
        "burn_rate_per_min": snap.db.burn_rate_per_min,
        "pace": _pace_dict(snap.pace),
        "usage": _usage_dict(snap.db, scoped=snap.account is not None),
    }


def _account_dict(account: AccountInfo | None) -> dict[str, object] | None:
    """Map official account usage to its JSON shape, or None if unavailable."""
    if account is None:
        return None
    return {
        "email": account.email,
        "tier": account.tier,
        "sub_type": account.sub_type,
        "used": account.used,
        "limit": account.limit,
        "currency": account.currency,
        "next_reset": account.next_reset.isoformat(),
        "overage_used": account.overage_used,
        "overage_cap": account.overage_cap,
        "overage_enabled": account.overage_enabled,
    }


def _pace_dict(pace: PaceInfo | None) -> dict[str, object] | None:
    """Map pacing info to its JSON shape, or None if unavailable."""
    if pace is None:
        return None
    return {
        "mode": pace.mode,
        "allowance_per_day": pace.allowance_per_day,
        "can_spend_per_day": pace.can_spend_per_day,
        "today_fraction": pace.today_fraction,
        "days_until_reset": pace.days_until_reset,
        "days_elapsed": pace.days_elapsed,
        "projection_runout": (
            pace.projection_runout.isoformat() if pace.projection_runout else None
        ),
        "non_working_today": pace.non_working_today,
        "holidays_available": pace.holidays_available,
    }


def _usage_dict(db: DbSnapshot, *, scoped: bool) -> dict[str, object] | None:
    """Map the folder/model breakdown to its JSON shape, or None if empty."""
    if not db.by_folder_model:
        return None
    return {
        "scope": "this cycle" if scoped else "recent",
        "by_folder_model": [list(row) for row in db.by_folder_model],
        "total_credits": sum(amount for *_, amount in db.by_folder_model),
        "total_turns": sum(turns for _, _, turns, _ in db.by_folder_model),
    }


def _exit_code(snap: Snapshot) -> int:
    """Map a snapshot to a scripting-friendly exit code."""
    if snap.account_status == "error":
        return _EXIT_ERROR
    if snap.account is None or snap.account.limit <= 0:
        return _EXIT_INDETERMINATE
    fraction = snap.account.used / snap.account.limit
    if fraction >= 1.0:
        return _EXIT_AT_LIMIT
    if fraction >= _NEAR_LIMIT_FRACTION:
        return _EXIT_NEAR_LIMIT
    return _EXIT_OK
