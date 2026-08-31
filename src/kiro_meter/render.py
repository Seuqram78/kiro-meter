"""Render a Snapshot into a rich renderable for the live view."""

from __future__ import annotations

import math
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
# The scanner advances one column every this-many redraws, deliberately slower
# than one-per-frame so the motion stays a peripheral "still alive" cue rather
# than the most attention-grabbing thing on screen (design evaluation A39).
_SCANNER_STEP_FRAMES = 3
_USAGE_BAR_WIDTH = 16
_PCT_NEAR_LIMIT = 75.0
_PCT_AT_LIMIT = 100.0
# Width of the ``─`` rule drawn above the Total row, per column (A37).
_RULE_WIDTH = 8
_CR_RULE_WIDTH = 6
# A common folder prefix is only worth eliding when at least this many rows
# share it (a single row keeps its full, unambiguous path).
_MIN_SHARED_ROWS = 2
# Burn rate is shown to two significant figures (design A21); time-to-exhaustion
# is humanised with these minute conversions.
_SIG_FIGS = 2
_MIN_PER_H = 60
_MINUTES_PER_DAY = 1440
# Semantic style tokens, defined once here so the whole palette is auditable
# in a single place (the measured-contrast gate in lint.sh reads these) and
# call sites name intent rather than a raw colour. Two rules govern the
# choices, both from the design evaluation and both required to stay legible
# on the pale #f2ecbc theme without branching per background:
#
#   1. Named ANSI colours, never truecolour hex: a fixed #rrggbb triplet looks
#      the same regardless of the terminal's light/dark profile - fine on the
#      mode it was tuned against, unreadable on the other. Named colours are
#      palette indices the terminal profile remaps for us (the approach
#      lazygit's default theme uses throughout).
#   2. Only the low-luminance half of that palette carries meaning: plain
#      `red`/`green`/`yellow`/`cyan`/`magenta`/`blue`. The `bright_*` variants
#      are defined as high-luminance, so on a light background they collapse
#      toward the background's own luminance and fail WCAG contrast - they are
#      never used as a foreground and never as a background fill.
#
# STYLE_VALUE deliberately carries no hue: the credits column earns prominence
# by being the only bold column (Von Restorff isolation), not by colour, and
# default-foreground-plus-bold is the single most legible pairing on the theme.
STYLE_VALUE = "bold"
STYLE_MUTED = "italic"
STYLE_OVER = "red"
STYLE_UNDER = "green"
STYLE_NEAR = "yellow"
# The track (empty bar cells) and the muted `even` reference bar share a hue
# but not a value: the `even` fill uses the default foreground so its length is
# a real position-on-a-common-scale judgement, while the track stays dim. Both
# avoid the old `bright_black`, which was invisible as an empty-cell glyph on
# the light theme.
_TRACK_STYLE = "dim"
_EVEN_FILL_STYLE = ""
# The share bar and the active-sort-column highlight both use plain (not
# bright) cyan - the low-luminance member of the palette that survives the
# light theme.
_SHARE_STYLE = "cyan"
_SORT_COLUMN_STYLE = "cyan bold"
_LOGIN_HINT = "Kiro session expired - run kiro-cli (or kiro-cli user login) to refresh."
# Key bindings grouped by purpose - navigation, view, exit - so the eye scans
# three short clusters rather than one undifferentiated run of eight bindings
# (Hick's Law: grouping cuts choice time). The groups are rendered in order,
# separated by a divider.
_KEY_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "navigate",
        (
            ("↑↓", "scroll"),
            ("←→", "nesting"),
        ),
    ),
    (
        "view",
        (
            ("s", "sort"),
            ("m", "model"),
            ("l", "local"),
        ),
    ),
    (
        "exit",
        (("q", "quit"),),
    ),
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
    budget_groups = _budget_section(snap) if show_local else []
    table_group, row_window = _table_section(snap, ui) if show_local else ([], None)

    footer: list[RenderableType] = []
    if frame is not None:
        lf = live_footer if live_footer is not None else LiveFooterState()
        footer.append(_footer(snap, frame, lf))
    if ui is not None:
        footer.append(_key_hints(ui, row_window))

    body = _stack([official, *budget_groups, table_group, footer])
    return Panel(
        body,
        title=_title(snap, cfg),
        title_align="left",
        padding=(1, 2),
    )


def _budget_section(snap: Snapshot) -> list[list[RenderableType]]:
    """Return the Today / Pace / Burn blocks as separate stack groups.

    Each block is its own group so ``_stack`` separates every one of Plan /
    Today / Pace / Burn by exactly one blank line, giving the four semantic
    sections a uniform vertical rhythm instead of the old mix of a blank line
    after Plan and flush lines between the rest (design evaluation A36).
    """
    groups: list[list[RenderableType]] = [
        [_today_line(snap.db, snap.pace, today_flagged=snap.today_flagged)]
    ]
    if snap.pace is not None:
        groups.append(_pace_lines(snap.pace))
    if snap.db.burn_rate_per_min is not None:
        remaining = (
            max(snap.account.limit - snap.account.used, 0.0)
            if snap.account is not None
            else None
        )
        groups.append([_burn_line(snap.db.burn_rate_per_min, remaining)])
    return groups


def _table_section(
    snap: Snapshot, ui: LiveState | None
) -> tuple[list[RenderableType], tuple[int, int, int] | None]:
    """Build the usage table and its row window, or nothing when there's no data.

    Folder-merged is the default breakdown for both the live view and one-shot
    text rendering; ``[m]`` expands the live view to per-model rows (the JSON
    export keeps the full per-folder-model breakdown regardless). Merging
    happens here, after app.py has already collapsed folders to the active
    nesting level, so models are merged within folders that are already
    grouped rather than across folders that aren't.

    Zero-credit rows carry no information scent and are dropped before
    windowing (design evaluation A15); a breakdown that is *all* zeroes leaves
    the section empty rather than a table of noise.
    """
    if not snap.db.by_folder_model:
        return ([], None)
    scroll = ui.scroll if ui is not None else 0
    sort = ui.sort if ui is not None else "cr_desc"
    by_model = ui.by_model if ui is not None else False
    rows = (
        snap.db.by_folder_model if by_model else merge_models(snap.db.by_folder_model)
    )
    rows = _visible_credit_rows(rows)
    if not rows:
        return ([], None)
    row_window = _row_window(len(rows), scroll, _visible_rows(ui))
    table = _usage_table(
        rows,
        scoped=snap.account is not None,
        row_window=row_window,
        view=TableView(sort=sort, by_model=by_model, emphasize=ui is not None),
    )
    return ([table], row_window)


def _visible_credit_rows(rows: tuple[UsageRow, ...]) -> tuple[UsageRow, ...]:
    """Drop rows that spent no credits (design evaluation A15).

    A zero-credit row - an `unknown`-model, `0.00`-cr, `0%` line - advertises
    nothing and consumes a table row that a real folder could use. Turn counts
    still roll into the Total via ``_usage_table``'s own sum over the rows it
    is handed, so filtering here also removes their turns from the total; that
    is intentional, the visible table and its Total describe the same set of
    rows.
    """
    return tuple(row for row in rows if row.credits > 0.0)


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
    """Render the nesting level and scroll position, then the grouped key bindings.

    State facts (nesting, row window) and available actions are visually
    separated, and the actions are split into navigation / view / exit
    clusters so eight bindings read as three short groups rather than one run
    (Hick's Law). Each key is bracketed so it can't be misread as part of its
    own label (e.g. ``l`` next to ``local``).
    """
    nesting_label = "full" if ui.nesting >= FULL_NESTING else str(ui.nesting)
    state_parts = [f"nesting {nesting_label}"]
    if row_window is not None:
        start, end, total = row_window
        if total:
            state_parts.append(f"rows {start + 1}-{end} of {total}")
    text = Text(" · ".join(state_parts), style=STYLE_MUTED)
    for _label, bindings in _KEY_GROUPS:
        text.append("  │  ", style=STYLE_MUTED)
        _append_key_group(text, bindings, ui)
    return text


def _append_key_group(
    text: Text, bindings: tuple[tuple[str, str], ...], ui: LiveState
) -> None:
    """Append one navigation/view/exit cluster of key bindings to ``text``."""
    for i, (key, label) in enumerate(bindings):
        if i:
            text.append(" ")
        text.append(f"[{key}]", style="bold cyan")
        text.append(f" {label}", style=STYLE_MUTED)
        if key == "s":
            text.append(f" ({_SORT_LABEL[ui.sort]})", style=STYLE_MUTED)
        if key == "m":
            text.append(f" ({_BREAKDOWN_LABEL[ui.by_model]})", style=STYLE_MUTED)


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
            ("● ", "green"),
            ("live", STYLE_MUTED),
            ("   ", STYLE_MUTED),
            (spinner, "dim cyan"),
            (" refreshing…", STYLE_MUTED),
            (f"   updated {updated}", STYLE_MUTED),
        )
    pos = _scanner_position(frame, _SCANNER_WIDTH)
    secs = int(live.seconds_until_refresh)
    return Text.assemble(
        ("● ", "green"),
        ("live", STYLE_MUTED),
        ("   next in ", STYLE_MUTED),
        (f"{secs}s", STYLE_MUTED),
        ("  ", STYLE_MUTED),
        ("·" * pos, "dim"),
        ("•", "dim cyan"),
        ("·" * (_SCANNER_WIDTH - pos - 1), "dim"),
        (f"   updated {updated}", STYLE_MUTED),
    )


def _scanner_position(frame: int, width: int) -> int:
    """Bounce a single marker slowly back and forth across ``[0, width - 1]``.

    The marker advances one column every ``_SCANNER_STEP_FRAMES`` redraws
    rather than every frame: motion is the strongest preattentive channel
    (Ware), so spending it on a mere countdown out-shouts the actual verdict
    lines above. Slowing the sweep keeps the "still alive" cue without letting
    it dominate attention (design evaluation A39).
    """
    period = 2 * (width - 1)
    step = (frame // _SCANNER_STEP_FRAMES) % period
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
        return Text("Plan: official limit unavailable", style="italic")
    return _plan_gauge(snap.account)


def _plan_gauge(account: AccountInfo) -> RenderableType:
    """Render the used/limit bar with the reset date and remaining balance."""
    pct = account.used / account.limit * _PERCENT if account.limit else 0.0
    style = _usage_style(pct)
    filled, empty = _bar(pct / _PERCENT)
    reset = account.next_reset.strftime("%b %d")
    remaining = max(account.limit - account.used, 0.0)
    return Text.assemble(
        ("Plan  ", STYLE_VALUE),
        (filled, style),
        (empty, _TRACK_STYLE),
        (f"  {account.used:.2f} / {account.limit:.2f} cr  ", STYLE_VALUE),
        (f"{pct:.0f}%\n", f"bold {style}"),
        (
            f"      resets {reset} (official)  ·  {remaining:.2f} cr left this cycle",
            STYLE_MUTED,
        ),
    )


def _usage_style(pct: float) -> str:
    """Muted-green under load, amber near the limit, red at or over it."""
    if pct >= _PCT_AT_LIMIT:
        return STYLE_OVER
    if pct >= _PCT_NEAR_LIMIT:
        return STYLE_NEAR
    return STYLE_UNDER


def _today_line(
    db: DbSnapshot, pace: PaceInfo | None, *, today_flagged: bool
) -> RenderableType:
    """Render today's spend and the one budget that answers "can I spend more".

    The figure comes from the API-usage baseline when an account is
    available (see ``app._resolve_today``), else it falls back to the local
    session-file sum. The primary answer is ``actual`` - what is genuinely
    still available today once banked slack counts - and it is the only
    colour-coded, ✓/✗-carrying row. ``even`` is shown beneath it as a muted
    *reference* rate (not a verdict): the flat rate an even spender would sit
    at, with today's multiple of it named, so the number is context for why
    the two disagree rather than a second competing alarm.
    """
    source = "official" if pace is not None else "local"
    flag = "  ⚠ local sum exceeds API total" if today_flagged else ""
    head = Text(
        f"Today  {db.today_credits:.2f} cr  ({db.today_turns} turns, {source}){flag}"
    )
    if pace is None or pace.allowance_per_day is None:
        return head
    rows = [
        _actual_row(pace.today_budget, db.today_credits, horizon=_today_horizon(pace)),
        _even_reference_row(pace.allowance_per_day, db.today_credits),
    ]
    return Group(head, *[row for row in rows if row is not None])


def _today_horizon(pace: PaceInfo) -> str:
    """Name the denominator behind today's ``actual`` budget (design A6/A18).

    ``today_budget`` spreads what was left at the start of today across today
    and every whole day still to come - ``days_forecast`` of them - so the
    horizon is "today + N days".
    """
    days = pace.days_forecast
    if days <= 0:
        return "today only"
    return f"today + {days} {_plural(days, 'day')}"


def _actual_row(
    budget: float | None, spent: float, *, horizon: str
) -> RenderableType | None:
    """Render the primary "actual" budget: today's real ceiling, colour-verdicted.

    This is the one row that answers "can I keep spending" against credits
    that genuinely exist, so it alone carries the green/amber/red verdict and
    the ✓/✗ glyph (a real binary: inside today's ceiling or over it - design
    A9). The horizon that forms the denominator is named inline (A6/A18).
    """
    if budget is None:
        return None
    pct = spent / budget * _PERCENT if budget else 0.0
    filled, empty = _bar(pct / _PERCENT)
    left = budget - spent
    within = left >= 0
    icon = "✓" if within else "✗"
    balance = (
        f"{left:.2f} cr left of {budget:.2f}/day"
        if within
        else f"{-left:.2f} cr over {budget:.2f}/day"
    )
    style = _usage_style(pct)
    return Text.assemble(
        ("      actual  ", STYLE_MUTED),
        (filled, style),
        (empty, _TRACK_STYLE),
        (f"  {icon} {pct:>4.0f}%  ", f"bold {style}"),
        (f"{balance}  ({horizon})", STYLE_MUTED),
    )


def _even_reference_row(allowance: float, spent: float) -> RenderableType:
    """Render the muted ``even`` *reference* rate - not a verdict (design A10/A11).

    ``even`` reads far "over" for anyone who banks slack early and spends it
    later, which is the normal pattern this tool exists to support, so
    promoting it to an alarm only trains alarm fatigue. It is shown with the
    default-foreground reference bar and no colour verdict, no ✓/✗, and no
    "over" wording - just today's multiple of the flat even rate, as a
    reference point for the actual row above it.
    """
    multiple = spent / allowance if allowance else 0.0
    filled, empty = _bar(multiple)
    return Text.assemble(
        ("      even    ", STYLE_MUTED),
        (filled, _EVEN_FILL_STYLE),
        (empty, _TRACK_STYLE),
        (f"  ref {allowance:.2f}/day", STYLE_MUTED),
        (f"  ({multiple:.1f}x today)", STYLE_MUTED),
    )


def _plural(count: int, noun: str) -> str:
    """``noun`` for a count of 1, ``noun + 's'`` otherwise (design A19)."""
    return noun if count == 1 else f"{noun}s"


def _pace_lines(pace: PaceInfo) -> list[RenderableType]:
    """Render the Pace block: remaining headroom, projected runout, and the days.

    Pace states *capacity*, not an instruction (design A8): "N cr of headroom"
    rather than "spend N cr/day", so a large final-day figure reads as room
    left, not a directive to burn it. The projected runout (design A13) is the
    genuine Level-3 situation-awareness input - when credits are exhausted at
    the current pace - and is shown when the trend actually runs out before
    reset. The days line names the phase of the cycle in words.
    """
    lines: list[RenderableType] = [_pace_headline(pace)]
    runout = _runout_line(pace)
    if runout is not None:
        lines.append(runout)
    lines.append(Text(_days_line(pace), style=STYLE_MUTED))
    return lines


def _days_line(pace: PaceInfo) -> str:
    """Phrase the cycle phase without the "1 days" defect (design A19/A20)."""
    gone, forecast = pace.days_gone, pace.days_forecast
    gone_txt = f"{gone} {_plural(gone, 'day')} done"
    forecast_txt = f"{forecast} {_plural(forecast, 'day')} left after today"
    return f"      {gone_txt}, today in progress, {forecast_txt}"


def _runout_line(pace: PaceInfo) -> RenderableType | None:
    """Render the projected credit-exhaustion date, when the trend runs out early."""
    if pace.projection_runout is None:
        return None
    when = pace.projection_runout.astimezone().strftime("%b %d")
    return Text(
        f"      ✗ at today's pace, credits run out {when} - before reset",
        style=f"bold {STYLE_OVER}",
    )


def _pace_headline(pace: PaceInfo) -> RenderableType:
    """Render remaining headroom as capacity, not an instruction (design A8)."""
    remaining = pace.can_spend_credits
    if pace.if_done_today_per_day is None:
        return Text(
            "Pace  last day of the cycle - nothing left to average", style=STYLE_MUTED
        )
    rate = pace.if_done_today_per_day
    horizon = f"{pace.days_forecast} {_plural(pace.days_forecast, 'day')} after today"
    banked = f"  ·  {remaining:.2f} cr banked vs even" if remaining is not None else ""
    text = f"Pace  {rate:.2f} cr/day of headroom over the {horizon}{banked}"
    return Text(text, style=STYLE_MUTED)


def _burn_line(burn_rate_per_min: float, remaining: float | None) -> RenderableType:
    """Render the recent burn rate as time-to-exhaustion, not just raw cr/min.

    ``15.917 cr/min`` implies milli-credit accuracy on a volatile local
    estimate - false precision - so the rate is shown to two significant
    figures (design A21). More importantly the raw rate is Level-1 telemetry;
    the actionable Level-3 reading is how long the remaining balance lasts at
    that rate (design A14), appended when a remaining balance is known and the
    rate is non-zero.
    """
    rate = _two_sig_figs(burn_rate_per_min)
    text = f"Burn  {rate} cr/min (local)"
    if remaining is not None and burn_rate_per_min > 0:
        text += f"  ·  ~{_humanize_minutes(remaining / burn_rate_per_min)} left"
    return Text(text, style=STYLE_MUTED)


def _two_sig_figs(value: float) -> str:
    """Format ``value`` to two significant figures, trailing zeros trimmed."""
    if value == 0:
        return "0"
    digits = _SIG_FIGS - 1 - math.floor(math.log10(abs(value)))
    rounded = round(value, max(digits, 0))
    text = f"{rounded:.{max(digits, 0)}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _humanize_minutes(minutes: float) -> str:
    """Render a duration in minutes as a compact "~Nh Mm" / "~Nm" string."""
    total = round(minutes)
    if total >= _MINUTES_PER_DAY:
        return f"{total // _MINUTES_PER_DAY}d {total % _MINUTES_PER_DAY // _MIN_PER_H}h"
    if total >= _MIN_PER_H:
        return f"{total // _MIN_PER_H}h {total % _MIN_PER_H}m"
    return f"{total}m"


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
    "model": {"no_wrap": True, "style": STYLE_MUTED},
    "share": {"no_wrap": True, "style": STYLE_MUTED},
    # Padding column: the secondary absorber for any residual width `expand`
    # adds once the folder column (ratio=3) has taken its share - keeps
    # model/bar tight on the left and cr/turns tight against the right edge,
    # at any terminal width.
    "pad": {"ratio": 1},
    "cr": {"justify": "right"},
    "turns": {"justify": "right", "style": STYLE_MUTED},
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
    within the argument-count limit. ``emphasize`` covers the live-view-only
    active-sort-column highlight (row striping was removed - design
    evaluation A31 - so at eight rows column alignment carries the grid on
    its own).
    """

    sort: TableSort = "cr_desc"
    by_model: bool = False
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

    The common leading path segment shared by every folder is elided from the
    rows and named once in the title (design evaluation A17), so scanning
    reaches the distinguishing segment immediately. A ``─`` rule row separates
    the Total from the data it summarises (A37); Total carries its own bold
    emphasis and picks the highlight up from the active sort column, so the
    sorted column stays identifiable on the one row users scan to check
    totals.

    ``emphasize`` turns the live-view-only sort highlight off, which is what
    one-shot rendering passes.
    """
    sort, by_model, emphasize = view.sort, view.by_model, view.emphasize
    peak = max(row.credits for row in rows)
    total_credits = sum(row.credits for row in rows)
    prefix = _common_folder_prefix(rows)
    sorted_rows = _sorted_rows(rows, sort)
    start, end, _total = (
        row_window if row_window is not None else (0, len(rows), len(rows))
    )
    scope = "this cycle" if scoped else "recent"
    grouping = "folder & model" if by_model else "folder"
    title = f"Usage by {grouping} ({scope})"
    if prefix:
        title += f" - under {prefix}"
    table = Table(
        title=title,
        title_justify="left",
        title_style=STYLE_MUTED,
        box=None,
        pad_edge=False,
        padding=(0, 2, 0, 0),
        expand=True,
    )
    keys = _column_keys(by_model=by_model)
    _add_columns(table, keys, sort, emphasize=emphasize)
    for row in sorted_rows[start:end]:
        cells = _data_cells(row, peak=peak, total_credits=total_credits, prefix=prefix)
        table.add_row(*(cells[key] for key in keys))
    _add_total_row(table, keys, rows=rows, total_credits=total_credits, scoped=scoped)
    return table


def _add_total_row(
    table: Table,
    keys: tuple[str, ...],
    *,
    rows: tuple[UsageRow, ...],
    total_credits: float,
    scoped: bool,
) -> None:
    """Append a ``─`` rule row and the bold Total row (design evaluation A37).

    When scoped to the billing cycle the Total's credits are the same figure
    as the Plan gauge's ``used``; naming that identity turns the row into a
    cross-check between the local sum and the official number (design A22).
    """
    rule: dict[str, RenderableType] = dict.fromkeys(_COLUMN_KEYS, "")
    rule |= {"folder": "─" * _RULE_WIDTH, "cr": "─" * _CR_RULE_WIDTH}
    table.add_row(*(rule[key] for key in keys), style=STYLE_MUTED)
    label = "Total (= Plan used)" if scoped else "Total"
    totals: dict[str, RenderableType] = dict.fromkeys(_COLUMN_KEYS, "")
    totals |= {
        "folder": label,
        "cr": f"{total_credits:.2f}",
        "turns": str(sum(row.turns for row in rows)),
    }
    table.add_row(*(totals[key] for key in keys), style="bold")


def _common_folder_prefix(rows: tuple[UsageRow, ...]) -> str:
    """The deepest ancestor path shared by every row's folder, or "".

    Compared segment-by-segment (not character-by-character) so it never
    splits a folder name mid-word, and only returned when it actually shortens
    the rows - a single row, or rows sharing only the root anchor, keep their
    full paths.
    """
    parts_per_row = [Path(row.folder).parts for row in rows]
    if len(parts_per_row) < _MIN_SHARED_ROWS:
        return ""
    shared: list[str] = []
    for segments in zip(*parts_per_row, strict=False):
        if len(set(segments)) != 1:
            break
        shared.append(segments[0])
    if len(shared) <= 1:  # nothing beyond the root/drive anchor is common
        return ""
    return str(Path(*shared))


def _elide_prefix(folder: str, prefix: str) -> str:
    """Strip a common ``prefix`` from ``folder``, marking the cut with ``…/``."""
    if not prefix:
        return folder
    try:
        relative = Path(folder).relative_to(prefix)
    except ValueError:
        return folder
    return f"…/{relative}"


def _data_cells(
    row: UsageRow, *, peak: float, total_credits: float, prefix: str = ""
) -> dict[str, RenderableType]:
    """One data row's cell for every possible column, keyed by column."""
    proportion = row.credits / peak if peak else 0.0
    share_pct = row.credits / total_credits * _PERCENT if total_credits else 0.0
    return {
        "folder": _elide_prefix(row.folder, prefix),
        "model": row.model,
        "share": Text(_proportion_bar(proportion, share_pct), style=_SHARE_STYLE),
        "pad": "",
        "cr": Text(f"{row.credits:.2f}", style=STYLE_VALUE),
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
    """Return the (filled, empty) segments of a fixed-width bar for ``fraction``.

    A fraction above 1.0 would otherwise render as a plain full bar
    indistinguishable from exactly 100% - the graphical-integrity failure the
    545% bar exhibited (design A29). When the scale is exceeded the final
    filled cell becomes a ``▸`` overflow cap so a saturated-and-overflowing bar
    is visually distinct from one that merely reached the top; the magnitude
    itself is carried by the adjacent multiplier/percentage text.
    """
    if fraction > 1.0:
        return "▓" * (_BAR_WIDTH - 1) + "▸", ""
    clamped = max(0.0, fraction)
    filled = round(clamped * _BAR_WIDTH)
    return "▓" * filled, "░" * (_BAR_WIDTH - filled)
