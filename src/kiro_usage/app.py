"""Assemble snapshots and drive the one-shot and live views."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from rich.console import Console
from rich.live import Live

from kiro_usage.account import (
    NeedsLoginError,
    fetch_account_info,
    load_token,
    token_expired,
)
from kiro_usage.db import build_db_snapshot, load_conversations
from kiro_usage.models import Snapshot
from kiro_usage.pace import compute_pace
from kiro_usage.render import render_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, tzinfo
    from pathlib import Path

    from kiro_usage.models import AccountInfo, AccountStatus, AppConfig
    from kiro_usage.pace import HolidayProvider

_NEAR_LIMIT_FRACTION = 0.9
_EXIT_OK = 0
_EXIT_NEAR_LIMIT = 10
_EXIT_AT_LIMIT = 11
_EXIT_INDETERMINATE = 20
_EXIT_ERROR = 30


@dataclass(frozen=True)
class RunContext:
    """Shared dependencies threaded through the snapshot pipeline."""

    db_path: Path
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
    db = build_db_snapshot(load_conversations(ctx.db_path), now=now, tz=ctx.tz)
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
    """Poll local spend and refresh the official limit on a slower cadence."""
    account: AccountInfo | None = None
    status: AccountStatus = "disabled"
    last_fetch = 0.0
    with Live(auto_refresh=False, screen=False) as live:
        try:
            while True:
                now = now_fn()
                monotonic = time.monotonic()
                if (
                    last_fetch == 0.0
                    or monotonic - last_fetch >= cfg.limit_refresh_seconds
                ):
                    account, status = resolve_account(cfg, ctx, now=now)
                    last_fetch = monotonic
                snap = build_snapshot(
                    cfg, ctx, account=account, account_status=status, now=now
                )
                live.update(render_snapshot(snap, cfg), refresh=True)
                time.sleep(cfg.refresh_seconds)
        except KeyboardInterrupt:
            pass


def _as_dict(snap: Snapshot) -> dict[str, object]:
    """Flatten a snapshot into a JSON-serialisable dict."""
    account, pace = snap.account, snap.pace
    return {
        "account_status": snap.account_status,
        "today_credits": snap.db.today_credits,
        "today_turns": snap.db.today_turns,
        "burn_rate_per_min": snap.db.burn_rate_per_min,
        "used": account.used if account else None,
        "limit": account.limit if account else None,
        "allowance_per_day": pace.allowance_per_day if pace else None,
        "actual_per_day": pace.actual_per_day if pace else None,
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
