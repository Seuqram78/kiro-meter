"""Tests for rendering snapshots to text."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console

from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot, PaceInfo, Snapshot
from kiro_usage.render import render_snapshot

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
_CONSOLE_WIDTH = 80


def _render(snap: Snapshot) -> str:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    return console.export_text()


def _db() -> DbSnapshot:
    return DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=(
            ("/home/me/proj-a", "sonnet-4.5", 8, 0.21),
            ("/home/me/proj-b", "haiku-4.5", 12, 0.10),
        ),
        recent=(),
        approx=True,
    )


def _account() -> AccountInfo:
    return AccountInfo(
        email="u@e.com",
        tier="KIRO FREE",
        sub_type="FREE",
        used=11.21,
        limit=50.0,
        overage_used=0.0,
        overage_cap=10000.0,
        overage_rate=0.04,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=3,
        currency="USD",
        fetched_at=_NOW,
    )


def _pace() -> PaceInfo:
    return PaceInfo(
        mode="calendar",
        allowance_per_day=1.61,
        can_spend_per_day=11.37,
        today_fraction=0.15,
        days_until_reset=4.0,
        days_elapsed=27.0,
        projection_runout=None,
        non_working_today=False,
        holidays_available=True,
    )


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


def test_needs_login_banner_shown_and_local_still_rendered() -> None:
    """Expired session shows a banner but keeps local spend."""
    snap = Snapshot(_db(), None, "needs_login", None, _NOW)
    text = _render(snap)
    assert "kiro-cli" in text.lower()
    assert "0.31" in text


def _footer_of(snap: Snapshot, countdown: float) -> str:
    console = Console(width=_CONSOLE_WIDTH, record=True)
    console.print(render_snapshot(snap, AppConfig(), countdown=countdown))
    return console.export_text().splitlines()[-2]


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
