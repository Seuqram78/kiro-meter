"""Tests for rendering snapshots to text."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console

from kiro_meter.models import AppConfig, Snapshot
from kiro_meter.render import render_snapshot
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


def _footer_of(snap: Snapshot, countdown: float) -> str:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig(), countdown=countdown))
    lines = [
        line for line in console.export_text().splitlines() if "next reading" in line
    ]
    return lines[0]


def test_footer_shows_live_status_and_next_reading() -> None:
    """A countdown renders the live status line with the next-reading meter."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    footer = _footer_of(snap, 0.0)
    assert "live" in footer
    assert "next reading" in footer
    assert "Ctrl-C" in footer


def test_no_footer_without_countdown() -> None:
    """One-shot rendering (no countdown) has no live footer."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    assert "next reading" not in _render(snap)


def test_countdown_bar_fills_over_time() -> None:
    """A larger countdown fraction fills more of the next-reading bar."""
    snap = Snapshot(_db(), _account(), "ok", _pace(), _NOW)
    early = _footer_of(snap, 0.1).count("█")
    late = _footer_of(snap, 0.9).count("█")
    assert late > early
