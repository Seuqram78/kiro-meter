"""Tests for pace computation."""

from __future__ import annotations

from datetime import UTC, datetime

from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot
from kiro_usage.pace import compute_pace

_NOW = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
_NDIGITS = 2
_TARGET_REMAINING = 36.0
_TARGET_DAYS = 17.0


def _account(used: float, limit: float) -> AccountInfo:
    return AccountInfo(
        email="u@e.com",
        tier="FREE",
        sub_type="FREE",
        used=used,
        limit=limit,
        overage_used=0.0,
        overage_cap=0.0,
        overage_rate=0.0,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=17,
        currency="USD",
        fetched_at=_NOW,
    )


def _db(today: float) -> DbSnapshot:
    return DbSnapshot(today, 1, today, 1, None, (), (), (), approx=True)


def test_target_and_actual_pace_calendar() -> None:
    """Target uses remaining/days-left, actual uses used/days-elapsed."""
    pace = compute_pace(
        _account(used=14.0, limit=50.0), _db(1.0), AppConfig(), now=_NOW
    )
    assert pace.mode == "calendar"
    assert pace.target_per_day is not None
    assert round(pace.target_per_day, _NDIGITS) == round(
        _TARGET_REMAINING / _TARGET_DAYS, _NDIGITS
    )
    assert pace.actual_per_day is not None
    assert pace.projection_runout is not None


def test_pace_none_when_cycle_just_reset() -> None:
    """When days_elapsed < 0.5 actual pace is None (no divide blow-up)."""
    now = datetime(2026, 7, 1, 1, 0, tzinfo=UTC)
    pace = compute_pace(_account(used=0.0, limit=50.0), _db(0.0), AppConfig(), now=now)
    assert pace.actual_per_day is None
