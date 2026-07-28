"""Command-line entry point for kiro-usage."""

from __future__ import annotations

import argparse
import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx

from kiro_usage.app import RunContext, run_live, run_once
from kiro_usage.config import (
    CONFIG_PATH,
    load_config,
    run_first_time_setup,
    save_config,
)
from kiro_usage.db import DEFAULT_DB_PATH
from kiro_usage.pace import NagerHolidayProvider

if TYPE_CHECKING:
    from kiro_usage.models import AppConfig


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, resolve config, and run the requested view.

    Args:
        argv: Argument list (defaults to ``sys.argv`` when None).

    Returns:
        A process exit code.
    """
    args = _parse_args(argv)
    cfg = _resolve_config(args)
    local_tz = datetime.now(UTC).astimezone().tzinfo
    tz = ZoneInfo(cfg.timezone) if cfg.timezone else local_tz
    with httpx.Client() as client:
        holidays = (
            NagerHolidayProvider(client=client, cache_dir=CONFIG_PATH.parent)
            if cfg.workdays
            else None
        )
        ctx = RunContext(
            db_path=DEFAULT_DB_PATH, client=client, tz=tz, holidays=holidays
        )
        if args.once:
            return run_once(cfg, ctx, now=datetime.now(UTC), as_json=args.json)
        run_live(cfg, ctx, now_fn=lambda: datetime.now(UTC))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Build the argument parser and parse ``argv``."""
    parser = argparse.ArgumentParser(prog="kiro-usage", description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="print one snapshot and exit"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON (implies --once)"
    )
    parser.add_argument(
        "--no-account", action="store_true", help="skip the official limit"
    )
    parser.add_argument("--refresh", type=int, help="live refresh interval in seconds")
    parser.add_argument("--timezone", help="timezone for the 'today' boundary")
    parser.add_argument(
        "--reconfigure", action="store_true", help="re-run first-time setup"
    )
    parsed = parser.parse_args(argv)
    if parsed.json:
        parsed.once = True
    return parsed


def _resolve_config(args: argparse.Namespace) -> AppConfig:
    """Load or create config, then apply command-line overrides."""
    cfg = load_config()
    if cfg is None or args.reconfigure:
        cfg = run_first_time_setup()
        save_config(cfg)
    return dataclasses.replace(
        cfg,
        use_account=cfg.use_account and not args.no_account,
        refresh_seconds=args.refresh or cfg.refresh_seconds,
        timezone=args.timezone or cfg.timezone,
    )
