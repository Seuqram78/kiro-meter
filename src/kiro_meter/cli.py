"""Command-line entry point for kiro-meter."""

from __future__ import annotations

import argparse
import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx

from kiro_meter.app import RunContext, run_live, run_once
from kiro_meter.baseline import DEFAULT_STATE_DB_PATH
from kiro_meter.config import (
    CONFIG_PATH,
    load_config,
    run_first_time_setup,
    save_config,
)
from kiro_meter.db import DEFAULT_DB_PATH, DEFAULT_SESSIONS_DIR
from kiro_meter.models import AppConfig
from kiro_meter.pace import HolidayUnavailableError, NagerHolidayProvider

if TYPE_CHECKING:
    from collections.abc import Callable


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, resolve config, and run the requested view.

    Args:
        argv: Argument list (defaults to ``sys.argv`` when None).

    Returns:
        A process exit code.
    """
    args = _parse_args(argv)
    with httpx.Client() as client:
        cfg = _resolve_config(args, client=client)
        local_tz = datetime.now(UTC).astimezone().tzinfo
        tz = ZoneInfo(cfg.timezone) if cfg.timezone else local_tz
        holidays = (
            NagerHolidayProvider(client=client, cache_dir=CONFIG_PATH.parent)
            if cfg.workdays
            else None
        )
        ctx = RunContext(
            db_path=DEFAULT_DB_PATH,
            sessions_dir=DEFAULT_SESSIONS_DIR,
            client=client,
            tz=tz,
            holidays=holidays,
            state_db_path=DEFAULT_STATE_DB_PATH,
        )
        if args.once:
            return run_once(cfg, ctx, now=datetime.now(UTC), as_json=args.json)
        run_live(cfg, ctx, now_fn=lambda: datetime.now(UTC))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Build the argument parser and parse ``argv``."""
    parser = argparse.ArgumentParser(prog="kiro-meter", description=__doc__)
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


def _resolve_config(args: argparse.Namespace, *, client: httpx.Client) -> AppConfig:
    """Load or create config, then apply command-line overrides.

    First-run setup is interactive; if no input is available it returns None and
    we fall back to defaults without persisting, so a later interactive run still
    prompts.
    """
    cfg = load_config()
    if cfg is None or args.reconfigure:
        chosen = run_first_time_setup(list_regions=_region_lister(client))
        if chosen is not None:
            cfg = chosen
            save_config(cfg)
    if cfg is None:
        cfg = AppConfig()
    return dataclasses.replace(
        cfg,
        use_account=cfg.use_account and not args.no_account,
        refresh_seconds=args.refresh or cfg.refresh_seconds,
        timezone=args.timezone or cfg.timezone,
    )


def _region_lister(client: httpx.Client) -> Callable[[str], list[str]]:
    """Build a region lister backed by the holiday API for the current year."""
    provider = NagerHolidayProvider(client=client, cache_dir=CONFIG_PATH.parent)
    year = datetime.now(UTC).year

    def lister(country: str) -> list[str]:
        """Return the country's subdivision codes, or none if unavailable."""
        try:
            return provider.available_regions(country, year)
        except HolidayUnavailableError:
            return []

    return lister
