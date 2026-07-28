"""Render a Snapshot into a rich renderable for the live view."""

from __future__ import annotations

from pathlib import Path
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
_COUNTDOWN_WIDTH = 12
_USAGE_BAR_WIDTH = 8
_FOLDER_WIDTH = 22
_EIGHTHS = 8
_PARTIALS = "▏▎▍▌▋▊▉"
_PCT_NEAR_LIMIT = 75.0
_PCT_AT_LIMIT = 100.0
_LOGIN_HINT = (
    "Kiro session expired - run kiro-cli (or kiro-cli user login) "
    "to refresh, then press r."
)


def render_snapshot(
    snap: Snapshot,
    cfg: AppConfig,
    *,
    countdown: float | None = None,
) -> RenderableType:
    """Turn a Snapshot into a titled panel of usage sections.

    Args:
        snap: The merged snapshot to display.
        cfg: Runtime configuration (title and refresh interval).
        countdown: Fraction (0-1) of the way to the next reading. When set,
            a live status line with the next-reading meter is drawn. ``None``
            (one-shot) draws no footer.

    Returns:
        A rich renderable ready to hand to ``Console.print`` or ``Live``.
    """
    official: list[RenderableType] = [_account_section(snap)]

    budget: list[RenderableType] = [_today_line(snap.db, snap.pace)]
    if snap.pace is not None:
        budget.extend(_pace_lines(snap.pace))
    if snap.db.burn_rate_per_min is not None:
        budget.append(_burn_line(snap.db.burn_rate_per_min))

    groups = [official, budget]
    if snap.db.by_folder_model:
        groups.append([_usage_table(snap.db)])
    if countdown is not None:
        groups.append([_footer(snap, countdown)])

    body = _stack(groups)
    return Panel(
        body,
        title=_title(snap, cfg),
        title_align="left",
        padding=(1, 2),
    )


def _stack(groups: list[list[RenderableType]]) -> RenderableType:
    """Join non-empty groups vertically, separated by a blank line."""
    rendered: list[RenderableType] = []
    for group in groups:
        if not group:
            continue
        if rendered:
            rendered.append(Text(""))
        rendered.extend(group)
    return Group(*rendered)


def _footer(snap: Snapshot, countdown: float) -> RenderableType:
    """Render the live status line: a green dot, next-reading meter, and time."""
    updated = snap.generated_at.astimezone().strftime("%H:%M:%S")
    meter = _sweep_bar(countdown, _COUNTDOWN_WIDTH)
    return Text.assemble(
        ("● ", "bold green"),
        ("live", "green"),
        ("   next reading ", "dim"),
        (f"▕{meter}▏", "cyan"),
        (f"   updated {updated}   Ctrl-C to quit", "dim"),
    )


def _sweep_bar(fraction: float, width: int) -> str:
    """Render a smooth partial-cell bar filling ``fraction`` of ``width``."""
    eighths = round(max(0.0, min(fraction, 1.0)) * width * _EIGHTHS)
    full, remainder = divmod(eighths, _EIGHTHS)
    cells = "█" * full
    if remainder:
        cells += _PARTIALS[remainder - 1]
    return cells.ljust(width, "░")


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
    """Render the used/limit bar with the reset date, coloured by usage state."""
    pct = account.used / account.limit * _PERCENT if account.limit else 0.0
    style = _usage_style(pct)
    bar = _bar(pct / _PERCENT)
    reset = account.next_reset.strftime("%b %d")
    return Text.assemble(
        ("Plan  ", "bold"),
        (bar, style),
        (f"  {account.used:.2f} / {account.limit:.2f} cr  ", ""),
        (f"{pct:.0f}%\n", f"bold {style}"),
        (f"      resets {reset} (official)", "dim"),
    )


def _usage_style(pct: float) -> str:
    """Green under load, amber near the limit, red at or over it."""
    if pct >= _PCT_AT_LIMIT:
        return "red"
    if pct >= _PCT_NEAR_LIMIT:
        return "yellow"
    return "green"


def _today_line(db: DbSnapshot, pace: PaceInfo | None) -> RenderableType:
    """Render today's spend, with a pace bar when a target exists."""
    if pace is not None and pace.today_fraction is not None:
        bar = _bar(pace.today_fraction)
        pct = pace.today_fraction * _PERCENT
        line = f"Today {bar}  {db.today_credits:.2f} cr  ({pct:.0f}% allowance, local)"
        return Text(line)
    return Text(f"Today  {db.today_credits:.2f} cr  ({db.today_turns} turns, local)")


def _pace_lines(pace: PaceInfo) -> list[RenderableType]:
    """Render the allowance and can-spend pace lines."""
    lines: list[RenderableType] = []
    if pace.allowance_per_day is not None:
        allowance = f"Pace  allowance {pace.allowance_per_day:.2f} cr/day (even budget)"
        lines.append(Text(allowance, style="dim"))
    if pace.can_spend_per_day is not None:
        can_spend = (
            f"      can spend {pace.can_spend_per_day:.2f} cr/day (rest of cycle)"
        )
        lines.append(Text(can_spend, style="dim"))
    return lines


def _burn_line(burn_rate_per_min: float) -> RenderableType:
    """Render the recent burn rate."""
    return Text(f"Burn  {burn_rate_per_min:.3f} cr/min (local)", style="dim")


def _usage_table(db: DbSnapshot) -> RenderableType:
    """Render a bar chart of credits grouped by folder and model."""
    peak = max(amount for *_, amount in db.by_folder_model)
    table = Table(
        title="Usage by folder & model (local)",
        title_justify="left",
        title_style="dim",
        box=None,
        pad_edge=False,
        padding=(0, 2, 0, 0),
    )
    table.add_column(
        "folder", overflow="ellipsis", max_width=_FOLDER_WIDTH, no_wrap=True
    )
    table.add_column("model", no_wrap=True, style="dim")
    table.add_column("", no_wrap=True)
    table.add_column("cr", justify="right")
    table.add_column("turns", justify="right", style="dim")
    for folder, model, turns, amount in db.by_folder_model:
        proportion = amount / peak if peak else 0.0
        table.add_row(
            _short_folder(folder),
            model,
            Text(_proportion_bar(proportion), style="cyan"),
            f"{amount:.2f}",
            str(turns),
        )
    return table


def _short_folder(folder: str) -> str:
    """Abbreviate the home directory to ``~`` for a compact folder label."""
    home = str(Path.home())
    if folder == home or folder.startswith(home + "/"):
        return "~" + folder[len(home) :]
    return folder


def _proportion_bar(fraction: float) -> str:
    """Render a small solid bar sized to ``fraction`` of the column width."""
    filled = round(max(0.0, min(fraction, 1.0)) * _USAGE_BAR_WIDTH)
    return "▇" * filled + " " * (_USAGE_BAR_WIDTH - filled)


def _bar(fraction: float) -> str:
    """Render a fixed-width text progress bar for ``fraction`` in [0, 1]."""
    clamped = max(0.0, min(fraction, 1.0))
    filled = round(clamped * _BAR_WIDTH)
    return "▓" * filled + "░" * (_BAR_WIDTH - filled)
