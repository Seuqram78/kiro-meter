"""Select a calendar month and generate a standalone usage report."""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from kiro_meter.app import resolve_account
from kiro_meter.db import (
    build_db_snapshot,
    collapse_by_nesting,
    load_conversations,
    max_displayed_folder_depth,
    merge_models,
)
from kiro_meter.report_render import render_report

if TYPE_CHECKING:
    from datetime import tzinfo

    from kiro_meter.app import RunContext
    from kiro_meter.models import AccountInfo, AppConfig, ConversationRow, UsageRow

_MS_PER_SECOND = 1000


@dataclass(frozen=True)
class ReportData:
    """Month-scoped local allocations and an optional current account reading."""

    year: int
    month: int
    nesting: int
    generated_at: datetime
    timezone: tzinfo
    projects: tuple[UsageRow, ...]
    models: tuple[UsageRow, ...]
    daily: tuple[tuple[UsageRow, ...], ...]
    account: AccountInfo | None
    account_status: str


def _row_time(row: ConversationRow, tz: tzinfo) -> datetime:
    """Convert a session's last update time into the reporting timezone."""
    return datetime.fromtimestamp(row.updated_at_ms / _MS_PER_SECOND, tz=tz)


def _choose(console: Console, title: str, options: list[str]) -> int:
    """Prompt for a numbered choice, retrying invalid input."""
    console.print(title, markup=False)
    for index, label in enumerate(options, start=1):
        console.print(f"  {index}) {label}", markup=False)
    while True:
        answer = console.input(f"Choose 1-{len(options)}: ").strip()
        if answer.isascii() and answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        console.print("Enter one of the listed numbers.", markup=False)


def _select_month(
    rows: list[ConversationRow], tz: tzinfo, console: Console
) -> tuple[int, int]:
    """Offer years and months represented in the retained local records."""
    dates = {_row_time(row, tz).date() for row in rows}
    years = sorted({day.year for day in dates}, reverse=True)
    year = years[_choose(console, "Report year", [str(year) for year in years])]
    months = sorted({day.month for day in dates if day.year == year})
    month = months[
        _choose(console, "Report month", [calendar.month_name[m] for m in months])
    ]
    return year, month


def build_report(
    rows: list[ConversationRow],
    selection: tuple[int, int, int],
    *,
    now: datetime,
    tz: tzinfo,
    account_result: tuple[AccountInfo | None, str] = (None, "unavailable"),
) -> ReportData:
    """Aggregate selected-month sessions at one nesting level, including zero days."""
    year, month, nesting = selection
    selected = [
        row
        for row in rows
        if (_row_time(row, tz).year, _row_time(row, tz).month) == (year, month)
        and row.updated_at_ms <= now.timestamp() * _MS_PER_SECOND
    ]
    snapshot = build_db_snapshot(selected, now=now, tz=tz)
    models = collapse_by_nesting(snapshot.by_folder_model, nesting)
    local_now = now.astimezone(tz)
    days = calendar.monthrange(year, month)[1]
    if (year, month) == (local_now.year, local_now.month):
        days = local_now.day
    daily = []
    for day in range(1, days + 1):
        day_rows = [row for row in selected if _row_time(row, tz).day == day]
        day_snapshot = build_db_snapshot(day_rows, now=now, tz=tz)
        daily.append(
            merge_models(collapse_by_nesting(day_snapshot.by_folder_model, nesting))
        )
    return ReportData(
        year,
        month,
        nesting,
        now,
        tz,
        merge_models(models),
        models,
        tuple(daily),
        *account_result,
    )


def _generate_report(
    cfg: AppConfig, ctx: RunContext, now: datetime, console: Console
) -> int:
    """Collect three choices, fetch applicable account data, and write PDF."""
    rows = [
        row
        for row in load_conversations(ctx.sessions_dir)
        if row.updated_at_ms <= now.timestamp() * _MS_PER_SECOND
    ]
    if not rows:
        console.print("No retained CLI sessions are available for reporting.")
        return 1
    year, month = _select_month(rows, ctx.tz, console)
    selected = [
        row
        for row in rows
        if (_row_time(row, ctx.tz).year, _row_time(row, ctx.tz).month) == (year, month)
    ]
    snapshot = build_db_snapshot(selected, now=now, tz=ctx.tz)
    maximum = max_displayed_folder_depth(snapshot.by_folder_model)
    nesting = 1 + _choose(
        console,
        f"Folder nesting level (maximum {maximum}; counted from the root)",
        [
            f"{level} — {_nesting_preview(snapshot.by_folder_model, level)}"
            for level in range(1, maximum + 1)
        ],
    )
    account, status = None, "historical"
    local_now = now.astimezone(ctx.tz)
    if (year, month) == (local_now.year, local_now.month):
        account, status = _report_account(cfg, ctx, now)
    report = build_report(
        rows,
        (year, month, nesting),
        now=now,
        tz=ctx.tz,
        account_result=(account, status),
    )
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    output = Path.cwd() / f"kiro-usage-{year}-{month:02d}-n{nesting}-{stamp}.pdf"
    content = render_report(report)
    with output.open("xb") as target:
        target.write(content)
    console.print(f"Report saved: {output}", markup=False)
    return 0


def _report_account(
    cfg: AppConfig, ctx: RunContext, now: datetime
) -> tuple[AccountInfo | None, str]:
    """Keep reporting available when the account token store is absent or broken."""
    try:
        return resolve_account(cfg, ctx, now=now)
    except OSError, sqlite3.Error, ValueError:
        return None, "error"


def _nesting_preview(rows: tuple[UsageRow, ...], level: int) -> str:
    """Show a concrete folder grouping before the user selects a level."""
    grouped = merge_models(collapse_by_nesting(rows, level))
    return ", ".join(row.folder for row in grouped[:3])


def run_report(cfg: AppConfig, ctx: RunContext, *, now: datetime) -> int:
    """Run the report flow, handling cancellation and filesystem errors."""
    console = Console()
    try:
        return _generate_report(cfg, ctx, now, console)
    except EOFError, KeyboardInterrupt:
        console.print("\nReport generation cancelled.")
        return 130
    except OSError as exc:
        console.print(f"Could not generate report: {exc}", markup=False)
        return 1
