"""Tests for pace computation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx

from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot
from kiro_usage.pace import NagerHolidayProvider, compute_pace

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
_NDIGITS = 2
_TARGET_REMAINING = 36.0
_TARGET_DAYS = 17.0
_EXPECTED_WORKING_DAYS = 5


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


def _holidays_payload() -> list[dict[str, object]]:
    return [{"date": "2026-07-04", "global": True, "counties": None}]


def test_working_days_excludes_weekends_and_holidays(tmp_path: Path) -> None:
    """Working-day count skips Sat/Sun and public holidays."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json=_holidays_payload())
        )
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    count = provider.working_days_between(
        date(2026, 6, 29), date(2026, 7, 6), country="US", region=None
    )
    assert count == _EXPECTED_WORKING_DAYS


def test_workday_mode_sets_mode_and_uses_provider(tmp_path: Path) -> None:
    """Workday mode is reflected in PaceInfo.mode."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    cfg = AppConfig(workdays=True, country="US")
    pace = compute_pace(
        _account(14.0, 50.0), _db(1.0), cfg, now=_NOW, holidays=provider
    )
    assert pace.mode == "workday"
