"""Tests for rendering snapshots to text."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console

from kiro_meter.interaction import LiveState
from kiro_meter.models import AppConfig, DbSnapshot, Snapshot
from kiro_meter.render import _VISIBLE_ROWS, render_snapshot
from tests.conftest import account_info as _account
from tests.conftest import db_snapshot as _db
from tests.conftest import pace_info as _pace

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
_CONSOLE_WIDTH = 80


def _render(snap: Snapshot) -> str:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    return console.export_text()


def test_official_gauge_rendered_when_account_present() -> None:
    """The plan gauge shows used/limit and an official label."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    assert "11.21" in text
    assert "50" in text
    assert "official" in text.lower()


def test_usage_table_aggregates_folder_and_model() -> None:
    """The usage table shows each folder, its model, credits, and turns."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    assert "proj-a" in text
    assert "sonnet-4.5" in text
    assert "0.21" in text
    assert "12" in text  # turn count for proj-b/haiku


def test_usage_table_shows_total_row() -> None:
    """A Total row sums credits and turns across every folder/model group."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    assert "Total" in text
    assert "0.31" in text  # 0.21 + 0.10
    assert "20" in text  # 8 + 12


def test_usage_table_fills_console_width() -> None:
    """The usage table expands to the full panel width, not just its content."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    lines = [line for line in text.splitlines() if "proj-a" in line]
    assert len(lines[0].rstrip()) >= _CONSOLE_WIDTH - 1


def test_needs_login_banner_shown_and_local_still_rendered() -> None:
    """Expired session shows a banner but keeps local spend."""
    snap = Snapshot(_db(), None, "needs_login", None, _NOW)
    text = _render(snap)
    assert "kiro-cli" in text.lower()
    assert "0.31" in text


def _footer_of(snap: Snapshot, frame: int) -> str:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig(), frame=frame))
    lines = [
        line for line in console.export_text().splitlines() if "next reading" in line
    ]
    return lines[0]


def test_footer_shows_live_status_and_next_reading() -> None:
    """A frame count renders the live status line with the sweeping marker."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    footer = _footer_of(snap, 0)
    assert "live" in footer
    assert "next reading" in footer
    assert "updated" in footer


def test_no_footer_without_frame() -> None:
    """One-shot rendering (no frame) has no live footer."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    assert "next reading" not in _render(snap)


def _marker_column(footer: str) -> int:
    """Index of the sweeping marker, ignoring the leading "● live" dot."""
    return footer.index("●", footer.index("next"))


def test_scanner_marker_advances_one_column_per_frame() -> None:
    """Each successive frame moves the marker exactly one column."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    early = _marker_column(_footer_of(snap, 1))
    late = _marker_column(_footer_of(snap, 2))
    assert late == early + 1


_MANY_ROWS = 20


def _db_many(count: int) -> DbSnapshot:
    """A DbSnapshot with `count` distinct, descending-credit folder rows."""
    rows = tuple((f"/proj-{i}", "haiku-4.5", 1, float(count - i)) for i in range(count))
    return DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=rows,
        recent=(),
        approx=True,
    )


def _render_windowed(snap: Snapshot, *, ui: LiveState) -> tuple[str, Console]:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig(), frame=0, ui=ui))
    return console.export_text(), console


def test_ui_none_renders_full_table_regardless_of_row_count() -> None:
    """Without ui, one-shot mode never truncates, even with hundreds of rows."""
    snap = Snapshot(_db_many(_MANY_ROWS), _account(), "ok", _pace(), _NOW)
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    text = console.export_text()
    for i in range(_MANY_ROWS):
        assert f"proj-{i}" in text


def test_live_view_windows_to_a_fixed_row_count_and_shows_row_hint() -> None:
    """The live view always shows a fixed row count, regardless of terminal size.

    This is deliberate, not derived from the console's reported height: some
    environments pin COLUMNS/LINES to a stale value, and a height-derived row
    count either over- or under-shoots the real screen in that case - most
    visibly with hundreds of folders, where an over-generous count ran the
    table past the bottom of the terminal.
    """
    snap = Snapshot(_db_many(_MANY_ROWS), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState())
    shown = sum(f"proj-{i}" in text for i in range(_MANY_ROWS))
    assert shown == _VISIBLE_ROWS
    assert f"of {_MANY_ROWS}" in text


def test_scroll_shifts_the_visible_window() -> None:
    """Increasing scroll changes which folder names are visible."""
    snap = Snapshot(_db_many(_MANY_ROWS), _account(), "ok", _pace(), _NOW)
    top_text, _ = _render_windowed(snap, ui=LiveState(scroll=0))
    scrolled_text, _ = _render_windowed(snap, ui=LiveState(scroll=5))
    assert "proj-0" in top_text
    assert "proj-0" not in scrolled_text


def test_total_and_bars_stable_while_scrolled() -> None:
    """The Total row and bar proportions use the full data, not the visible slice."""
    snap = Snapshot(_db_many(_MANY_ROWS), _account(), "ok", _pace(), _NOW)
    top_text, _ = _render_windowed(snap, ui=LiveState(scroll=0))
    scrolled_text, _ = _render_windowed(snap, ui=LiveState(scroll=5))
    total_line = next(line for line in top_text.splitlines() if "Total" in line)
    scrolled_total_line = next(
        line for line in scrolled_text.splitlines() if "Total" in line
    )
    assert total_line == scrolled_total_line


def test_show_local_false_hides_today_burn_and_table_but_keeps_gauge() -> None:
    """Hiding local sections leaves only the official plan gauge."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(show_local=False))
    assert "official" in text.lower()
    assert "Today" not in text
    assert "Burn" not in text
    assert "proj-a" not in text


def test_show_local_false_with_no_account_shows_only_banner() -> None:
    """The degenerate case: no account and local hidden leaves just the banner."""
    snap = Snapshot(_db(), None, "needs_login", None, _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(show_local=False))
    assert "kiro-cli" in text.lower()
    assert "0.31" not in text  # today's local spend is hidden


def test_key_hints_show_nesting_and_bindings() -> None:
    """The footer's key-hints line reports nesting and the key bindings."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(nesting=2))
    assert "nesting 2" in text
    assert "scroll" in text
    assert "nesting" in text.lower()
    assert "quit" in text
