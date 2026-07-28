"""Render a Snapshot into a rich renderable for the live view."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType

    from kiro_usage.models import (
        AccountInfo,
        AppConfig,
        DbSnapshot,
        PaceInfo,
        Snapshot,
    )

_BAR_WIDTH = 16
_PERCENT = 100.0
_LOGIN_HINT = (
    "Kiro session expired - run kiro-cli (or kiro-cli user login) "
    "to refresh, then press r."
)


def render_snapshot(snap: Snapshot, cfg: AppConfig) -> RenderableType:
    """Turn a Snapshot into a titled panel of usage sections.

    Args:
        snap: The merged snapshot to display.
        cfg: Runtime configuration (currently used for the title).

    Returns:
        A rich renderable ready to hand to ``Console.print`` or ``Live``.
    """
    sections: list[RenderableType] = [
        _account_section(snap),
        _today_line(snap.db, snap.pace),
    ]
    if snap.pace is not None:
        sections.extend(_pace_lines(snap.pace))
    if snap.db.burn_rate_per_min is not None:
        sections.append(_burn_line(snap.db.burn_rate_per_min))
    if snap.db.by_folder or snap.db.by_model:
        sections.append(_breakdowns(snap.db))
    if snap.db.recent:
        sections.append(_recent_table(snap.db))
    return Panel(Group(*sections), title=_title(snap, cfg), title_align="left")


def _title(snap: Snapshot, cfg: AppConfig) -> str:
    """Build the panel title from tier and pace mode."""
    tier = snap.account.tier if snap.account is not None else "Kiro Usage"
    mode = "workday" if cfg.workdays else "calendar"
    return f"{tier} - {mode} pacing"


def _account_section(snap: Snapshot) -> RenderableType:
    """Render the plan gauge, the login banner, or an unavailable note."""
    if snap.account_status == "needs_login":
        return Text(_LOGIN_HINT, style="yellow")
    if snap.account is None:
        return Text("Plan: official limit unavailable", style="dim")
    return _plan_gauge(snap.account)


def _plan_gauge(account: AccountInfo) -> RenderableType:
    """Render the used/limit bar with the reset date, labelled official."""
    pct = account.used / account.limit * _PERCENT if account.limit else 0.0
    bar = _bar(pct / _PERCENT)
    reset = account.next_reset.strftime("%b %d")
    return Text.assemble(
        ("Plan  ", "bold"),
        f"{bar}  {account.used:.2f} / {account.limit:.2f} cr  {pct:.0f}%\n",
        (f"      resets {reset} (official)", "dim"),
    )


def _today_line(db: DbSnapshot, pace: PaceInfo | None) -> RenderableType:
    """Render today's spend, with a pace bar when a target exists."""
    if pace is not None and pace.today_fraction is not None:
        bar = _bar(pace.today_fraction)
        pct = pace.today_fraction * _PERCENT
        line = f"Today {bar}  {db.today_credits:.2f} cr  ({pct:.0f}% allowance, local)"
        return Text(line)
    return Text(f"Today  {db.today_credits:.2f} cr  ({db.today_turns} turns, local)")


def _pace_lines(pace: PaceInfo) -> list[RenderableType]:
    """Render the allowance and actual pace lines."""
    lines: list[RenderableType] = []
    if pace.allowance_per_day is not None:
        allowance = f"Pace  allowance {pace.allowance_per_day:.2f} cr/day (even budget)"
        lines.append(Text(allowance, style="dim"))
    if pace.actual_per_day is not None:
        actual = f"      actual    {pace.actual_per_day:.2f} cr/day (affordable now)"
        lines.append(Text(actual, style="dim"))
    return lines


def _burn_line(burn_rate_per_min: float) -> RenderableType:
    """Render the recent burn rate."""
    return Text(f"Burn  {burn_rate_per_min:.3f} cr/min (local)", style="dim")


def _breakdowns(db: DbSnapshot) -> RenderableType:
    """Render side-by-side folder and model credit tables."""
    grid = Table.grid(padding=(0, 4))
    grid.add_row(
        _labelled_table("By folder", db.by_folder),
        _labelled_table("By model", db.by_model),
    )
    return grid


def _labelled_table(heading: str, rows: tuple[tuple[str, float], ...]) -> Table:
    """Build a two-column credits table with a heading."""
    table = Table(title=heading, title_justify="left", show_edge=False, pad_edge=False)
    table.add_column("name")
    table.add_column("cr", justify="right")
    for name, amount in rows:
        table.add_row(name, f"{amount:.2f}")
    return table


def _recent_table(db: DbSnapshot) -> RenderableType:
    """Render the most recent turns."""
    table = Table(title="Recent", title_justify="left", show_edge=False, pad_edge=False)
    table.add_column("folder")
    table.add_column("model")
    table.add_column("cr", justify="right")
    for row in db.recent:
        table.add_row(row.folder, row.model_id or "unknown", f"{row.credits:.3f}")
    return table


def _bar(fraction: float) -> str:
    """Render a fixed-width text progress bar for ``fraction`` in [0, 1]."""
    clamped = max(0.0, min(fraction, 1.0))
    filled = round(clamped * _BAR_WIDTH)
    return "▓" * filled + "░" * (_BAR_WIDTH - filled)
