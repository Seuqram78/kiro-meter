"""Render a Snapshot into a rich renderable for the live view."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kiro_meter.db import FULL_NESTING

if TYPE_CHECKING:
    from rich.console import Console, RenderableType

    from kiro_meter.interaction import LiveState
    from kiro_meter.models import (
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
_LOGIN_HINT = "Kiro session expired - run kiro-cli (or kiro-cli user login) to refresh."
# Table's own fixed rows (blank separator before it, title, header, blank
# separator before the total row, and the total row) plus the Panel's
# border and padding=(1, 2) top/bottom blank lines.
_TABLE_CHROME_LINES = 5
_PANEL_CHROME_LINES = 4
_KEY_HINTS_LINES = 1


def render_snapshot(
    snap: Snapshot,
    cfg: AppConfig,
    *,
    countdown: float | None = None,
    ui: LiveState | None = None,
    console: Console | None = None,
) -> RenderableType:
    """Turn a Snapshot into a titled panel of usage sections.

    Args:
        snap: The merged snapshot to display.
        cfg: Runtime configuration (title and refresh interval).
        countdown: Fraction (0-1) of the way to the next reading. When set,
            a live status line with the next-reading meter is drawn. ``None``
            (one-shot) draws no footer.
        ui: Live keyboard-driven state (scroll, nesting, local visibility).
            ``None`` (one-shot) renders every local section and the full,
            untruncated usage table, matching pre-interactive behaviour.
        console: The console the view is drawn to, used to size the
            scrollable table to the available height. Required together with
            ``ui`` to enable row windowing; ignored otherwise.

    Returns:
        A rich renderable ready to hand to ``Console.print`` or ``Live``.
    """
    show_local = ui is None or ui.show_local
    official: list[RenderableType] = [_account_section(snap)]

    budget: list[RenderableType] = []
    if show_local:
        budget.append(_today_line(snap.db, snap.pace))
        if snap.pace is not None:
            budget.extend(_pace_lines(snap.pace))
        if snap.db.burn_rate_per_min is not None:
            budget.append(_burn_line(snap.db.burn_rate_per_min))

    footer: list[RenderableType] = []
    if countdown is not None:
        footer.append(_footer(snap, countdown))

    table_group: list[RenderableType] = []
    row_window: tuple[int, int, int] | None = None
    if show_local and snap.db.by_folder_model:
        visible_rows = _visible_rows(official, budget, footer, ui=ui, console=console)
        scroll = ui.scroll if ui is not None else 0
        row_window = _row_window(len(snap.db.by_folder_model), scroll, visible_rows)
        table_group.append(
            _usage_table(
                snap.db, scoped=snap.account is not None, row_window=row_window
            )
        )

    if ui is not None:
        footer.append(_key_hints(ui, row_window))

    body = _stack([official, budget, table_group, footer])
    return Panel(
        body,
        title=_title(snap, cfg),
        title_align="left",
        padding=(1, 2),
    )


def _visible_rows(
    official: list[RenderableType],
    budget: list[RenderableType],
    footer: list[RenderableType],
    *,
    ui: LiveState | None,
    console: Console | None,
) -> int | None:
    """Return how many table rows fit, or None to render every row unwindowed."""
    if ui is None or console is None:
        return None
    chrome = _stack([official, budget, footer])
    chrome_height = len(console.render_lines(chrome, console.options, pad=False))
    overhead = (
        chrome_height + _TABLE_CHROME_LINES + _PANEL_CHROME_LINES + _KEY_HINTS_LINES
    )
    return max(1, console.size.height - overhead)


def _row_window(
    total: int, scroll: int, visible_rows: int | None
) -> tuple[int, int, int]:
    """Compute the visible (start, end, total) slice of a scrollable table.

    ``visible_rows=None`` disables windowing (one-shot mode) - the full
    range is always "visible".
    """
    if visible_rows is None or visible_rows >= total:
        return (0, total, total)
    max_start = max(0, total - visible_rows)
    start = max(0, min(scroll, max_start))
    return (start, start + visible_rows, total)


def _key_hints(
    ui: LiveState, row_window: tuple[int, int, int] | None
) -> RenderableType:
    """Render the nesting level, scroll position, and key bindings."""
    nesting_label = "full" if ui.nesting >= FULL_NESTING else str(ui.nesting)
    parts = [f"nesting {nesting_label}"]
    if row_window is not None:
        start, end, total = row_window
        if total:
            parts.append(f"rows {start + 1}-{end} of {total}")
    parts.append("↑↓ scroll  ←→ nesting  l local  q quit")
    return Text("   ".join(parts), style="dim")


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


def _usage_table(
    db: DbSnapshot,
    *,
    scoped: bool,
    row_window: tuple[int, int, int] | None = None,
) -> RenderableType:
    """Render a bar chart of credits grouped by folder and model.

    ``row_window`` (start, end, total), when given, draws only that slice of
    rows - but bar scaling and the Total row always use the full, unwindowed
    data, so they stay stable while scrolling.
    """
    all_rows = db.by_folder_model
    peak = max(amount for *_, amount in all_rows)
    start, end, _total = (
        row_window if row_window is not None else (0, len(all_rows), len(all_rows))
    )
    scope = "this cycle" if scoped else "recent"
    table = Table(
        title=f"Usage by folder & model ({scope})",
        title_justify="left",
        title_style="dim",
        box=None,
        pad_edge=False,
        padding=(0, 2, 0, 0),
        expand=True,
    )
    table.add_column("folder", overflow="fold", max_width=_FOLDER_WIDTH)
    table.add_column("model", no_wrap=True, style="dim")
    table.add_column("", no_wrap=True)
    table.add_column("cr", justify="right")
    table.add_column("turns", justify="right", style="dim")
    for folder, model, turns, amount in all_rows[start:end]:
        proportion = amount / peak if peak else 0.0
        table.add_row(
            _short_folder(folder),
            model,
            Text(_proportion_bar(proportion), style="cyan"),
            f"{amount:.2f}",
            str(turns),
        )
    total_credits = sum(amount for *_, amount in all_rows)
    total_turns = sum(turns for _, _, turns, _ in all_rows)
    table.add_row("", "", "", "", "")
    table.add_row(
        "Total", "", "", f"{total_credits:.2f}", str(total_turns), style="bold"
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
