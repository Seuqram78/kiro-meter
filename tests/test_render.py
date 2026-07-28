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
        by_folder=(("/proj-a", 0.21),),
        by_model=(("haiku", 0.13),),
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
        target_per_day=2.1,
        actual_per_day=1.9,
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


def test_needs_login_banner_shown_and_local_still_rendered() -> None:
    """Expired session shows a banner but keeps local spend."""
    snap = Snapshot(_db(), None, "needs_login", None, _NOW)
    text = _render(snap)
    assert "kiro-cli" in text.lower()
    assert "0.31" in text
