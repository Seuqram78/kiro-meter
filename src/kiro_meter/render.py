"""Render a Snapshot into a rich renderable for the live view."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from kiro_meter.db import FULL_NESTING, merge_models

if TYPE_CHECKING:
    from rich.console import RenderableType

    from kiro_meter.interaction import LiveState, TableSort
    from kiro_meter.models import (
        AccountInfo,
        AppConfig,
        DbSnapshot,
        PaceInfo,
        Snapshot,
        UsageRow,
    )

_BAR_WIDTH = 16
_PERCENT = 100.0
_SCANNER_WIDTH = 10
_USAGE_BAR_WIDTH = 16
_PCT_NEAR_LIMIT = 75.0
_PCT_AT_LIMIT = 100.0
# A truecolor hex, not a named/8-bit color: named ANSI colors (incl. "dim")
# are palette indices, and terminal themes commonly remap the 256-color
# greyscale ramp to a tinted shade - on the reporting terminal that turned
# bar tracks into a near-black/olive checkerboard. A #rrggbb triplet is sent
# as a direct 24-bit RGB escape, which themes can't remap.
_TRACK_STYLE = "#6c6c6c"
# Same reasoning as _TRACK_STYLE: truecolor hex, never a named/8-bit colour.
# The stripe is a background only and the sort highlight a foreground only, so
# the two combine on a sorted cell of a striped row without either being lost.
_STRIPE_STYLE = "on #262626"
_SORT_COLUMN_STYLE = "#87d7ff"
_LOGIN_HINT = "Kiro session expired - run kiro-cli (or kiro-cli user login) to refresh."
_KEY_BINDINGS = (
    ("↑↓", "scroll"),
    ("←→", "nesting"),
    ("s", "sort"),
    ("m", "model"),
    ("l", "local"),
    ("q", "quit"),
)
# Labels for the ``m`` breakdown toggle, shown next to its key hint.
_BREAKDOWN_LABEL = {True: "per model", False: "merged"}
# Fixed rather than derived from the terminal's reported height: some
# environments (corporate shells, VDI sessions) pin COLUMNS/LINES to a stale
# value, which made a height-derived row count either over- or under-shoot
# the real screen - most visibly with hundreds of folders, where an
# over-generous count ran the table past the bottom of the terminal.
_VISIBLE_ROWS = 10
_SPINNER_CHARS = ("↻", "↺")
# Short display strings for each sort state, shown in the key-hints line
# and as column header suffixes.
_SORT_LABEL: dict[str, str] = {
    "cr_desc": "cr ↓",
    "cr_asc": "cr ↑",
    "folder_asc": "folder ↑",
    "folder_desc": "folder ↓",
}


@dataclass(frozen=True)
class LiveFooterState:
    """Live-view footer state passed into render_snapshot during the live loop.

    Bundled into one object so render_snapshot stays within the argument-count
    limit while keeping ``frame`` separate (frame drives the scanner animation,
    which is independent of refresh timing).
    """

    seconds_until_refresh: float = 0.0
    refreshing: bool = False


def render_snapshot(
    snap: Snapshot,
    cfg: AppConfig,
    *,
    frame: int | None = None,
    ui: LiveState | None = None,
    live_footer: LiveFooterState | None = None,
) -> RenderableType:
    """Turn a Snapshot into a titled panel of usage sections.

    Args:
        snap: The merged snapshot to display.
        cfg: Runtime configuration (title and refresh interval).
        frame: Redraw counter for the live view, advancing the sweeping
            activity marker by one column each call. When set, a live status
            line is drawn. ``None`` (one-shot) draws no footer.
        ui: Live keyboard-driven state (scroll, nesting, local visibility).
            ``None`` (one-shot) renders every local section and the full,
            untruncated usage table, matching pre-interactive behaviour.
            Otherwise the usage table is windowed to a fixed row count.
        live_footer: Refresh state for the footer (countdown and in-progress
            indicator). ``None`` (one-shot) uses defaults (0s, not refreshing).

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
    if frame is not None:
        lf = live_footer if live_footer is not None else LiveFooterState()
        footer.append(_footer(snap, frame, lf))

    table_group, row_window = _table_section(snap, ui) if show_local else ([], None)

    if ui is not None:
        footer.append(_key_hints(ui, row_window))

    body = _stack([official, budget, table_group, footer])
    return Panel(
        body,
        title=_title(snap, cfg),
        title_align="left",
        padding=(1, 2),
    )


def _table_section(
    snap: Snapshot, ui: LiveState | None
) -> tuple[list[RenderableType], tuple[int, int, int] | None]:
    """Build the usage table and its row window, or nothing when there's no data.

    One-shot rendering (``ui is None``) always keeps the full
    per-folder-model breakdown; only the live view can merge models. Merging
    happens here, after app.py has already collapsed folders to the active
    nesting level, so models are merged within folders that are already
    grouped rather than across folders that aren't.
    """
    if not snap.db.by_folder_model:
        return ([], None)
    scroll = ui.scroll if ui is not None else 0
    sort = ui.sort if ui is not None else "cr_desc"
    by_model = ui.by_model if ui is not None else True
    rows = (
        snap.db.by_folder_model if by_model else merge_models(snap.db.by_folder_model)
    )
    row_window = _row_window(len(rows), scroll, _visible_rows(ui))
    table = _usage_table(
        rows,
        scoped=snap.account is not None,
        row_window=row_window,
        view=TableView(sort=sort, by_model=by_model, emphasize=ui is not None),
    )
    return ([table], row_window)


def _visible_rows(ui: LiveState | None) -> int | None:
    """Return the fixed live-view row count, or None to show every row (one-shot)."""
    return _VISIBLE_ROWS if ui is not None else None


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
    """Render the nesting level and scroll position, then the key bindings.

    State facts (nesting, row window) and available actions are visually
    separated, and each key is bracketed so it can't be misread as part of
    its own label (e.g. ``l`` next to ``local``).
    """
    nesting_label = "full" if ui.nesting >= FULL_NESTING else str(ui.nesting)
    state_parts = [f"nesting {nesting_label}"]
    if row_window is not None:
        start, end, total = row_window
        if total:
            state_parts.append(f"rows {start + 1}-{end} of {total}")
    text = Text(" · ".join(state_parts), style="dim")
    text.append(" │ ", style="dim")
    for i, (key, label) in enumerate(_KEY_BINDINGS):
        if i:
            text.append(" ")
        text.append(f"[{key}]", style="bold cyan")
        text.append(f" {label}", style="dim")
        if key == "s":
            text.append(f" ({_SORT_LABEL[ui.sort]})", style="dim")
        if key == "m":
            text.append(f" ({_BREAKDOWN_LABEL[ui.by_model]})", style="dim")
    return text


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


def _footer(
    snap: Snapshot,
    frame: int,
    live: LiveFooterState,
) -> RenderableType:
    """Render the live status line: a green dot, refresh state, and last-updated time.

    When a fetch is in progress (``live.refreshing``), a cycling spinner
    replaces the scanner so the user knows data is being loaded.  Otherwise
    a sweeping scanner animates within a fixed-width slot alongside a numeric
    countdown so the user knows exactly when the next refresh will fire.

    The scanner marker advances exactly one column per redraw (``frame`` is a
    plain counter, not a wall-clock reading), which is the smoothest motion
    possible on a character grid - deriving the position from elapsed seconds
    instead would need to round to a whole column on every tick, and unless
    the speed happens to divide the tick rate evenly that rounding shows up as
    an uneven hold-hold-jump cadence.
    """
    updated = snap.generated_at.astimezone().strftime("%H:%M:%S")
    if live.refreshing:
        spinner = _SPINNER_CHARS[frame % len(_SPINNER_CHARS)]
        return Text.assemble(
            ("● ", "bold green"),
            ("live", "green"),
            ("   ", "dim"),
            (spinner, "bold cyan"),
            (" refreshing…", "dim"),
            (f"   updated {updated}", "dim"),
        )
    pos = _scanner_position(frame, _SCANNER_WIDTH)
    secs = int(live.seconds_until_refresh)
    return Text.assemble(
        ("● ", "bold green"),
        ("live", "green"),
        ("   next in ", "dim"),
        (f"{secs}s", "bold cyan"),
        ("  ", "dim"),
        ("·" * pos, "dim"),
        ("●", "bold cyan"),
        ("·" * (_SCANNER_WIDTH - pos - 1), "dim"),
        (f"   updated {updated}", "dim"),
    )


def _scanner_position(frame: int, width: int) -> int:
    """Bounce a single marker back and forth across ``[0, width - 1]``."""
    period = 2 * (width - 1)
    step = frame % period
    return step if step < width else period - step


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
    filled, empty = _bar(pct / _PERCENT)
    reset = account.next_reset.strftime("%b %d")
    return Text.assemble(
        ("Plan  ", "bold"),
        (filled, style),
        (empty, _TRACK_STYLE),
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
        filled, empty = _bar(pace.today_fraction)
        pct = pace.today_fraction * _PERCENT
        return Text.assemble(
            "Today ",
            (filled, "cyan"),
            (empty, _TRACK_STYLE),
            (f"  {db.today_credits:.2f} cr  ({pct:.0f}% allowance, local)", ""),
        )
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


def _column_header(base: str, sort: TableSort, asc: TableSort, desc: TableSort) -> str:
    """Return ``base``, or the arrowed sort label when ``sort`` is this column's key."""
    if sort in (asc, desc):
        return _SORT_LABEL[sort]
    return base


def _folder_depth(folder: str) -> int:
    """Number of real path segments in ``folder``, excluding any root/drive anchor.

    Rows at the current nesting level aren't all the same depth - a folder
    shallower than the active nesting level passes through
    ``collapse_by_nesting`` unchanged - so a plain string sort would
    interleave shallow and deep paths by whatever they happen to spell.
    Sorting on depth first groups same-level folders together, matching
    what the nesting column actually shows.
    """
    path = Path(folder)
    return len(path.parts) - (1 if path.anchor else 0)


def _depth_then_name(row: UsageRow) -> tuple[int, str, str]:
    """Folder sort key: nesting depth first, then path, then model."""
    return (_folder_depth(row.folder), row.folder, row.model)


def _sorted_rows(rows: tuple[UsageRow, ...], sort: TableSort) -> tuple[UsageRow, ...]:
    """Reorder folder/model rows for display; ``rows`` itself is untouched."""
    if sort == "cr_desc":
        return tuple(sorted(rows, key=lambda r: r.credits, reverse=True))
    if sort == "cr_asc":
        return tuple(sorted(rows, key=lambda r: r.credits))
    if sort == "folder_asc":
        return tuple(sorted(rows, key=_depth_then_name))
    return tuple(sorted(rows, key=_depth_then_name, reverse=True))


# Every column the usage table can show, in display order. The model column is
# dropped entirely when models are merged - it is never blanked out.
_COLUMN_KEYS = ("folder", "model", "share", "pad", "cr", "turns")
# Which column each sort order sorts on, for the active-column highlight.
_SORT_COLUMN: dict[str, str] = {
    "cr_desc": "cr",
    "cr_asc": "cr",
    "folder_asc": "folder",
    "folder_desc": "folder",
}
_COLUMN_OPTIONS: dict[str, dict[str, object]] = {
    "folder": {"overflow": "fold", "ratio": 3},
    "model": {"no_wrap": True, "style": "dim"},
    "share": {"no_wrap": True, "style": "dim"},
    # Padding column: the secondary absorber for any residual width `expand`
    # adds once the folder column (ratio=3) has taken its share - keeps
    # model/bar tight on the left and cr/turns tight against the right edge,
    # at any terminal width.
    "pad": {"ratio": 1},
    "cr": {"justify": "right"},
    "turns": {"justify": "right", "style": "dim"},
}


def _column_keys(*, by_model: bool) -> tuple[str, ...]:
    """The columns to draw - the single source for both headers and row cells.

    Rich pads a short ``add_row`` with blank cells instead of raising, so rows
    built from their own hand-maintained lists would silently misalign against
    the headers (a cr value landing under the wrong one); every row is built
    from this tuple instead.
    """
    if by_model:
        return _COLUMN_KEYS
    return tuple(key for key in _COLUMN_KEYS if key != "model")


def _add_columns(
    table: Table, keys: tuple[str, ...], sort: TableSort, *, emphasize: bool
) -> None:
    """Add ``keys`` as columns, highlighting the one the table is sorted by."""
    headers = {
        "folder": _column_header("folder", sort, "folder_asc", "folder_desc"),
        "cr": _column_header("cr", sort, "cr_asc", "cr_desc"),
        "pad": "",
    }
    for key in keys:
        options = dict(_COLUMN_OPTIONS[key])
        if emphasize and key == _SORT_COLUMN[sort]:
            base = options.get("style", "")
            options["style"] = f"{base} {_SORT_COLUMN_STYLE}".strip()
            options["header_style"] = _SORT_COLUMN_STYLE
        table.add_column(headers.get(key, key), **options)


@dataclass(frozen=True)
class TableView:
    """How the usage table presents its rows: order, grouping, and emphasis.

    Bundled into one object (like LiveFooterState) so _usage_table stays
    within the argument-count limit. ``emphasize`` covers the two live-view
    only cues - row stripes and the active-sort-column highlight.
    """

    sort: TableSort = "cr_desc"
    by_model: bool = True
    emphasize: bool = True


_DEFAULT_VIEW = TableView()


def _usage_table(
    rows: tuple[UsageRow, ...],
    *,
    scoped: bool,
    row_window: tuple[int, int, int] | None = None,
    view: TableView = _DEFAULT_VIEW,
) -> Table:
    """Render a bar chart of credits grouped by folder, and by model when asked.

    ``row_window`` (start, end, total), when given, draws only that slice of
    rows - but bar scaling and the Total row always use the full, unwindowed
    data, so they stay stable while scrolling. ``sort`` reorders the rows
    before windowing; the Total row and bar peak still come from the unsorted
    ``rows``, so they're unaffected by sort order.

    Data rows alternate a background stripe by their absolute position, so a
    given row's stripe doesn't flip as the window scrolls past it. The spacer
    and Total rows are never striped, keeping Total visually apart. Total
    carries only its own bold emphasis and picks the highlight up from the
    active sort column, so the sorted column stays identifiable on the one row
    users scan to check totals - rather than being swallowed by Total's
    existing emphasis.

    ``emphasize`` turns both of those live-view cues off, which is what
    one-shot rendering passes: its output stays byte-for-byte what it was
    before stripes and sort highlighting existed (the sort arrow in the
    header predates them and still shows).
    """
    sort, by_model, emphasize = view.sort, view.by_model, view.emphasize
    peak = max(row.credits for row in rows)
    total_credits = sum(row.credits for row in rows)
    sorted_rows = _sorted_rows(rows, sort)
    start, end, _total = (
        row_window if row_window is not None else (0, len(rows), len(rows))
    )
    scope = "this cycle" if scoped else "recent"
    grouping = "folder & model" if by_model else "folder"
    table = Table(
        title=f"Usage by {grouping} ({scope})",
        title_justify="left",
        title_style="dim",
        box=None,
        pad_edge=False,
        padding=(0, 2, 0, 0),
        expand=True,
    )
    keys = _column_keys(by_model=by_model)
    _add_columns(table, keys, sort, emphasize=emphasize)
    for position, row in enumerate(sorted_rows[start:end], start=start):
        cells = _data_cells(row, peak=peak, total_credits=total_credits)
        stripe = _STRIPE_STYLE if emphasize and position % 2 else None
        table.add_row(*(cells[key] for key in keys), style=stripe)
    totals: dict[str, RenderableType] = dict.fromkeys(_COLUMN_KEYS, "")
    table.add_row(*(totals[key] for key in keys))
    totals |= {
        "folder": "Total",
        "cr": f"{total_credits:.2f}",
        "turns": str(sum(row.turns for row in rows)),
    }
    table.add_row(*(totals[key] for key in keys), style="bold")
    return table


def _data_cells(
    row: UsageRow, *, peak: float, total_credits: float
) -> dict[str, RenderableType]:
    """One data row's cell for every possible column, keyed by column."""
    proportion = row.credits / peak if peak else 0.0
    share_pct = row.credits / total_credits * _PERCENT if total_credits else 0.0
    return {
        "folder": row.folder,
        "model": row.model,
        "share": Text(_proportion_bar(proportion, share_pct), style="cyan"),
        "pad": "",
        "cr": f"{row.credits:.2f}",
        "turns": str(row.turns),
    }


def _proportion_bar(fraction: float, share_pct: float) -> str:
    """Render a solid bar sized to ``fraction`` of the peak row, plus ``share_pct``.

    The bar length and the percentage answer different questions on purpose:
    the bar shows how this row compares to the single largest row (so the
    top row is always a full bar), while the percentage shows this row's
    share of the *total* across all rows (so the percentages add up to
    ~100% down the column). The percentage is right-padded to a fixed width
    (e.g. ``  48%``) so the share column doesn't shift as values go from
    single to triple digits.
    """
    clamped = max(0.0, min(fraction, 1.0))
    filled = round(clamped * _USAGE_BAR_WIDTH)
    bar = "▇" * filled + " " * (_USAGE_BAR_WIDTH - filled)
    return f"{bar}  {share_pct:>4.0f}%"


def _bar(fraction: float) -> tuple[str, str]:
    """Return the (filled, empty) segments of a fixed-width bar for ``fraction``."""
    clamped = max(0.0, min(fraction, 1.0))
    filled = round(clamped * _BAR_WIDTH)
    return "▓" * filled, "░" * (_BAR_WIDTH - filled)
