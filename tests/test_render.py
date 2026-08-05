"""Tests for rendering snapshots to text."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.console import Console

from kiro_meter.interaction import LiveState
from kiro_meter.models import AppConfig, DbSnapshot, Snapshot, UsageRow

if TYPE_CHECKING:
    from rich.table import Table
from kiro_meter.render import (
    _SORT_COLUMN_STYLE,
    _STRIPE_STYLE,
    _VISIBLE_ROWS,
    TableView,
    _usage_table,
    render_snapshot,
)
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


def test_pace_lines_render_forecast_and_days() -> None:
    """The Pace block shows if-done-today, since-day-start, and the days count."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    assert "if done today" in text
    assert "since day start" in text
    assert "days gone" in text
    assert "days forecast" in text


def test_can_spend_ahead_of_pace_is_not_styled_as_over() -> None:
    """A positive can_spend_credits reads as being ahead of pace."""
    pace = replace(_pace(), can_spend_credits=11.37)
    snap = Snapshot(_db(), _account(), "ok", pace, _NOW)
    text = _render(snap)
    assert "ahead of pace" in text
    assert "over pace" not in text


def test_can_spend_negative_reads_as_over_pace() -> None:
    """A negative can_spend_credits reads as being over pace, sign stripped."""
    pace = replace(_pace(), can_spend_credits=-11.37)
    snap = Snapshot(_db(), _account(), "ok", pace, _NOW)
    text = _render(snap)
    assert "over pace" in text
    assert "11.37" in text
    assert "-11.37" not in text


def test_today_flagged_shows_warning_marker() -> None:
    """A flagged Today line warns that the local sum exceeds the API total."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW, today_flagged=True)
    text = _render(snap)
    assert "exceeds api total" in text.lower()


def test_today_not_flagged_has_no_warning_marker() -> None:
    """A non-flagged snapshot's Today line has no warning marker."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW, today_flagged=False)
    text = _render(snap)
    assert "exceeds" not in text.lower()


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
    lines = [line for line in console.export_text().splitlines() if "next in" in line]
    return lines[0]


def test_footer_shows_live_status_and_next_reading() -> None:
    """A frame count renders the live status line with the countdown."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    footer = _footer_of(snap, 0)
    assert "live" in footer
    assert "next in" in footer
    assert "updated" in footer


def test_no_footer_without_frame() -> None:
    """One-shot rendering (no frame) has no live footer."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    assert "next in" not in _render(snap)


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
    rows = tuple(
        UsageRow(f"/proj-{i}", "haiku-4.5", 1, float(count - i)) for i in range(count)
    )
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


def test_key_hints_show_active_sort() -> None:
    """The key-hints line shows the currently active sort next to [s]."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_asc"))
    assert "sort" in text
    assert "folder ↑" in text


def test_share_column_shows_percentage_of_total() -> None:
    """The share % is each row's portion of the column total, not of the peak row."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    # proj-a 0.21 / 0.31 total = 68%, proj-b 0.10 / 0.31 total = 32%
    assert "68%" in text
    assert "32%" in text


def test_default_sort_is_credits_descending_with_arrow_header() -> None:
    """One-shot rendering (no ui) defaults to cr-descending, with the arrow shown."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text = _render(snap)
    assert "cr ↓" in text
    lines = [line for line in text.splitlines() if "proj-a" in line or "proj-b" in line]
    assert "proj-a" in lines[0]  # higher credits (0.21) sorts first


def test_sort_folder_asc_reorders_rows_and_flips_header() -> None:
    """Cycling to folder_asc sorts rows alphabetically and updates the header."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_asc"))
    assert "folder ↑" in text
    assert "cr ↓" not in text
    assert "cr ↑" not in text
    lines = [line for line in text.splitlines() if "proj-a" in line or "proj-b" in line]
    assert "proj-a" in lines[0]  # "proj-a" < "proj-b" alphabetically


def test_sort_folder_desc_reverses_row_order() -> None:
    """folder_desc puts alphabetically-later folders first."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_desc"))
    assert "folder ↓" in text
    lines = [line for line in text.splitlines() if "proj-a" in line or "proj-b" in line]
    assert "proj-b" in lines[0]


def _db_mixed_depth() -> DbSnapshot:
    """Rows at different path depths, where alphabetical order disagrees with depth."""
    return DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=(
            UsageRow(
                "/home/a/b/c", "sonnet-4.5", 4, 0.05
            ),  # depth 4, alphabetically first
            UsageRow("/home/z", "haiku-4.5", 2, 0.02),  # depth 2, alphabetically last
        ),
        recent=(),
        approx=True,
    )


def test_sort_folder_asc_groups_by_nesting_depth_before_alphabetical() -> None:
    """A shallower folder sorts first even when it's alphabetically later."""
    snap = Snapshot(_db_mixed_depth(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_asc"))
    lines = [
        line for line in text.splitlines() if "/home/a/b/c" in line or "/home/z" in line
    ]
    assert "/home/z" in lines[0]  # depth 2 sorts before depth 4, despite "z" > "a"


def test_sort_folder_desc_groups_by_nesting_depth_before_alphabetical() -> None:
    """The deepest folder sorts first in descending order, regardless of spelling."""
    snap = Snapshot(_db_mixed_depth(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_desc"))
    lines = [
        line for line in text.splitlines() if "/home/a/b/c" in line or "/home/z" in line
    ]
    assert "/home/a/b/c" in lines[0]  # depth 4 sorts before depth 2


def test_sort_cr_asc_reverses_credit_order() -> None:
    """cr_asc puts the lowest-credit row first."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="cr_asc"))
    assert "cr ↑" in text
    lines = [line for line in text.splitlines() if "proj-a" in line or "proj-b" in line]
    assert "proj-b" in lines[0]  # lower credits (0.10) sorts first


def test_sort_does_not_change_total_row_or_shares() -> None:
    """Sort is purely presentational: Total and per-row shares stay stable."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    default_text, _ = _render_windowed(snap, ui=LiveState())
    sorted_text, _ = _render_windowed(snap, ui=LiveState(sort="folder_desc"))
    default_total = next(line for line in default_text.splitlines() if "Total" in line)
    sorted_total = next(line for line in sorted_text.splitlines() if "Total" in line)
    assert default_total == sorted_total
    assert "68%" in sorted_text
    assert "32%" in sorted_text


def test_folder_column_is_not_truncated_on_wide_terminal() -> None:
    """A long folder path isn't cut off or folded when there's ample width."""
    long_folder = "/home/me/a-genuinely-long-project-folder-name-for-testing"
    db = DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=(UsageRow(long_folder, "sonnet-4.5", 8, 0.21),),
        recent=(),
        approx=True,
    )
    snap = Snapshot(db, _account(), "ok", _pace(), _NOW)
    console = Console(width=160, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    text = console.export_text()
    assert long_folder in text


def _merged_db() -> DbSnapshot:
    """Two folders, one of them split across two models."""
    return DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=(
            UsageRow("/home/me/proj-a", "sonnet-4.5", 8, 0.21),
            UsageRow("/home/me/proj-a", "haiku-4.5", 4, 0.09),
            UsageRow("/home/me/proj-b", "haiku-4.5", 12, 0.10),
        ),
        recent=(),
        approx=True,
    )


def _table_of(
    db: DbSnapshot,
    *,
    row_window: tuple[int, int, int] | None = None,
    **view: object,
) -> Table:
    """Build the usage table directly, to inspect its columns and row styles."""
    return _usage_table(
        db.by_folder_model,
        scoped=True,
        row_window=row_window,
        view=TableView(**view),
    )


def _row_cells(table: Table) -> list[list[object]]:
    """Every row's cells, as a list per row."""
    return [
        list(row)
        for row in zip(*(list(col.cells) for col in table.columns), strict=True)
    ]


def test_model_column_is_omitted_when_models_are_merged() -> None:
    """Folder-only view drops the model column instead of showing it blank."""
    headers = [col.header for col in _table_of(_merged_db(), by_model=False).columns]
    assert not any("model" in str(header) for header in headers)
    assert any("model" in str(col.header) for col in _table_of(_merged_db()).columns)


def test_merged_view_shows_one_summed_row_per_folder() -> None:
    """Each folder appears once, with its per-model turns and credits summed."""
    snap = Snapshot(_merged_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(by_model=False))
    rows = [line for line in text.splitlines() if "proj-a" in line]
    assert len(rows) == 1
    assert "0.30" in rows[0]  # 0.21 + 0.09
    assert "12" in rows[0]  # 8 + 4 turns
    assert "sonnet-4.5" not in text


def test_per_model_rows_are_restored_when_the_breakdown_is_on() -> None:
    """The default view keeps each model's own row and values."""
    snap = Snapshot(_merged_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(by_model=True))
    assert "sonnet-4.5" in text
    assert "0.21" in text
    assert "0.09" in text


def test_every_row_has_the_same_cell_count_as_the_header() -> None:
    """Columns and row cells come from one decision, in both breakdown modes."""
    for by_model in (True, False):
        table = _table_of(_merged_db(), by_model=by_model)
        widths = {len(cells) for cells in _row_cells(table)}
        assert widths == {len(table.columns)}


def test_data_rows_alternate_stripes_by_absolute_position() -> None:
    """Striping follows the row's position in the whole table, not the window."""
    db = _db_many(_MANY_ROWS)
    unscrolled = _table_of(db, row_window=(0, 4, _MANY_ROWS))
    scrolled = _table_of(db, row_window=(1, 5, _MANY_ROWS))
    first_four = [row.style for row in unscrolled.rows[:4]]
    assert _STRIPE_STYLE in str(first_four[1])
    assert first_four[0] != first_four[1]
    assert first_four[0] == first_four[2]
    assert [row.style for row in scrolled.rows[:3]] == first_four[1:4]


def test_spacer_and_total_rows_are_not_striped() -> None:
    """The stripe only marks data rows, so the Total row still stands apart."""
    table = _table_of(_merged_db())
    spacer, total = table.rows[-2], table.rows[-1]
    assert _STRIPE_STYLE not in str(spacer.style)
    assert "bold" in str(total.style)
    assert _STRIPE_STYLE not in str(total.style)


def test_active_sort_column_is_highlighted() -> None:
    """The sorted column is styled, and the other sortable one isn't."""
    by_cr = {col.header: col for col in _table_of(_merged_db(), sort="cr_desc").columns}
    cr_col = next(col for header, col in by_cr.items() if "cr" in str(header))
    folder_col = next(col for header, col in by_cr.items() if "folder" in str(header))
    assert _SORT_COLUMN_STYLE in str(cr_col.style)
    assert _SORT_COLUMN_STYLE in str(cr_col.header_style)
    assert _SORT_COLUMN_STYLE not in str(folder_col.style)


def test_highlight_follows_the_sort_to_the_folder_column() -> None:
    """Sorting by folder moves the highlight off the cr column."""
    cols = {
        str(col.header): col
        for col in _table_of(_merged_db(), sort="folder_asc").columns
    }
    folder_col = next(col for header, col in cols.items() if "folder" in header)
    cr_col = next(col for header, col in cols.items() if "cr" in header)
    assert _SORT_COLUMN_STYLE in str(folder_col.style)
    assert _SORT_COLUMN_STYLE not in str(cr_col.style)


def test_total_row_keeps_the_sort_highlight_distinct_from_its_own_emphasis() -> None:
    """The Total row's sorted cell reads as sorted, not just as bold like its peers."""
    table = _table_of(_merged_db(), sort="cr_desc")
    cr_col = next(col for col in table.columns if "cr" in str(col.header))
    total_style = str(table.rows[-1].style)
    assert _SORT_COLUMN_STYLE not in total_style  # Total's own emphasis is plain bold
    assert _SORT_COLUMN_STYLE in str(cr_col.style)  # ...the column adds the highlight


def test_single_row_table_renders() -> None:
    """One data row: nothing to alternate against, and nothing breaks."""
    snap = Snapshot(_db_many(1), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState())
    assert "proj-0" in text


def test_empty_table_with_breakdown_toggled_off_renders() -> None:
    """No usage rows at all, models merged: the table section is simply skipped."""
    db = DbSnapshot(0.0, 0, 0.0, 0, None, (), (), approx=True)
    snap = Snapshot(db, _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(by_model=False))
    assert "Total" not in text


def test_key_hints_include_the_model_toggle_and_wrap_at_eighty_columns() -> None:
    """The [m] hint is present and the hint line still wraps rather than truncates."""
    snap = Snapshot(_merged_db(), _account(), "ok", _pace(), _NOW)
    text, _ = _render_windowed(snap, ui=LiveState(sort="folder_desc"))
    assert "[m]" in text
    assert "model" in text


def _ansi(style: str) -> str:
    """The ANSI colour body rich emits for a truecolor style like ``#262626``."""
    red, green, blue = (int(style[-6:][i : i + 2], 16) for i in (0, 2, 4))
    return f"{red};{green};{blue}"


def test_one_shot_output_has_no_stripes_or_sort_highlight() -> None:
    """Striping and the sort highlight are live-view only; one-shot is untouched."""
    snap = Snapshot(_merged_db(), _account(), "ok", _pace(), _NOW)
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    styled = console.export_text(styles=True)
    assert _ansi(_STRIPE_STYLE) not in styled
    assert _ansi(_SORT_COLUMN_STYLE) not in styled
    assert "cr ↓" in styled  # the pre-existing header arrow still shows


def test_live_view_output_does_stripe_and_highlight() -> None:
    """The same data in the live view carries both new visual cues."""
    snap = Snapshot(_merged_db(), _account(), "ok", _pace(), _NOW)
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig(), frame=0, ui=LiveState()))
    styled = console.export_text(styles=True)
    assert _ansi(_STRIPE_STYLE) in styled
    assert _ansi(_SORT_COLUMN_STYLE) in styled
