"""Tests for pace computation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx

from kiro_meter.models import AccountInfo, AppConfig, DbSnapshot
from kiro_meter.pace import NagerHolidayProvider, PaceExtras, compute_pace

if TYPE_CHECKING:
    from pathlib import Path

_NOW = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)
_NDIGITS = 4
_LIMIT = 50.0
_CYCLE_DAYS = 31.0
_REMAINING = 36.0
_DAYS_LEFT = 17.0
_DAYS_GONE = 14
_DAYS_FORECAST = 16
_DAYS_INTO_CYCLE = _DAYS_GONE + 1
_USED = 14.0
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
        currency="USD",
        fetched_at=_NOW,
    )


def _db(today: float) -> DbSnapshot:
    return DbSnapshot(today, 1, None, (), (), approx=True)


def test_allowance_and_forecast_pace_calendar() -> None:
    """Allowance is the even daily budget; if-done-today uses whole days left."""
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT), _db(1.0), AppConfig(), now=_NOW
    )
    assert pace.mode == "calendar"
    assert pace.allowance_per_day is not None
    # allowance = limit / cycle length (Jul 1 -> Aug 1 = 31 days)
    assert round(pace.allowance_per_day, _NDIGITS) == round(
        _LIMIT / _CYCLE_DAYS, _NDIGITS
    )
    # at exact midnight Jul 15: 14 whole days gone, today, 16 forecast (31 total)
    assert pace.days_gone == _DAYS_GONE
    assert pace.days_forecast == _DAYS_FORECAST
    # if done today = remaining 36 over the 16 whole days after today
    assert pace.if_done_today_per_day is not None
    assert round(pace.if_done_today_per_day, _NDIGITS) == round(
        _REMAINING / _DAYS_FORECAST, _NDIGITS
    )
    # can spend = (whole days into the cycle, today included) * allowance - used
    assert pace.can_spend_credits is not None
    expected_can_spend = _DAYS_INTO_CYCLE * (_LIMIT / _CYCLE_DAYS) - _USED
    assert round(pace.can_spend_credits, _NDIGITS) == round(
        expected_can_spend, _NDIGITS
    )
    # no baseline passed in -> since_day_start is unavailable
    assert pace.since_day_start_per_day is None


def test_can_spend_negative_when_over_ideal_pace() -> None:
    """Spending past the ideal-pace target makes can_spend_credits negative."""
    over_pace_used = 30.0
    pace = compute_pace(
        _account(used=over_pace_used, limit=_LIMIT), _db(1.0), AppConfig(), now=_NOW
    )
    assert pace.can_spend_credits is not None
    assert pace.can_spend_credits < 0


def test_since_day_start_uses_baseline_numerator() -> None:
    """since_day_start uses this morning's baseline, not the live used total."""
    baseline = 10.0
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT),
        _db(1.0),
        AppConfig(),
        now=_NOW,
        extras=PaceExtras(today_baseline=baseline),
    )
    assert pace.since_day_start_per_day is not None
    assert round(pace.since_day_start_per_day, _NDIGITS) == round(
        (_LIMIT - baseline) / _DAYS_LEFT, _NDIGITS
    )


def test_today_budget_spreads_banked_slack_over_remaining_days() -> None:
    """Underspending raises today's ceiling above the flat even allowance."""
    baseline = 10.0
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT),
        _db(1.0),
        AppConfig(),
        now=_NOW,
        extras=PaceExtras(today_baseline=baseline),
    )
    assert pace.today_budget is not None
    assert pace.allowance_per_day is not None
    assert round(pace.today_budget, _NDIGITS) == round(
        (_LIMIT - baseline) / _DAYS_LEFT, _NDIGITS
    )
    assert pace.today_budget > pace.allowance_per_day


def test_today_budget_falls_back_to_live_used_without_baseline() -> None:
    """With no baseline yet the ceiling still renders, off the live used total."""
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT), _db(1.0), AppConfig(), now=_NOW
    )
    assert pace.since_day_start_per_day is None
    assert pace.today_budget is not None
    assert round(pace.today_budget, _NDIGITS) == round(
        (_LIMIT - _USED) / _DAYS_LEFT, _NDIGITS
    )


def test_today_budget_none_when_reset_imminent() -> None:
    """Past the last whole day of the cycle there is no budget left to spread."""
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    pace = compute_pace(
        _account(used=40.0, limit=_LIMIT), _db(0.0), AppConfig(), now=now
    )
    assert pace.today_budget is None


def test_if_done_today_none_when_reset_imminent() -> None:
    """When under a day remains, if-done-today is None (no whole days left)."""
    now = datetime(2026, 7, 31, 23, 50, tzinfo=UTC)
    pace = compute_pace(
        _account(used=40.0, limit=_LIMIT), _db(0.0), AppConfig(), now=now
    )
    assert pace.days_forecast == 0
    assert pace.if_done_today_per_day is None


def test_projection_runout_projects_before_reset_at_a_high_pace() -> None:
    """A pace that exhausts credits before reset yields a projected date (A12)."""
    # 36 cr remaining at 12 cr/day today -> ~3 days -> Jul 18, before Aug 1.
    heavy_day = 12.0
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT),
        _db(heavy_day),
        AppConfig(),
        now=_NOW,
    )
    assert pace.projection_runout is not None
    assert pace.projection_runout < _account(_USED, _LIMIT).next_reset


def test_projection_runout_is_none_when_trend_lands_after_reset() -> None:
    """A modest pace that outlasts the cycle projects no early runout (A12)."""
    light_day = 0.5  # 36 cr / 0.5 = 72 days, well past the Aug 1 reset
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT),
        _db(light_day),
        AppConfig(),
        now=_NOW,
    )
    assert pace.projection_runout is None


def test_projection_runout_is_none_without_spend() -> None:
    """No spend today means no meaningful projection (avoids divide-by-zero)."""
    pace = compute_pace(
        _account(used=_USED, limit=_LIMIT), _db(0.0), AppConfig(), now=_NOW
    )
    assert pace.projection_runout is None


def test_projection_runout_is_none_when_credit_days_exceed_calendar_days() -> None:
    """Conservative daily spend that doesn't exhaust credits before reset has no alarm.

    When daily spend is below the safe budget, the credit-days exceeds calendar
    days available, so no projection alarm is shown (fix for false positives).
    Matches the real-world bug: 1156 cr remaining, 90 cr today, 9 days left.
    90 * 9 = 810 cr < 1156 cr, so the user is safe. Before the fix, this
    incorrectly projected a runout on Sep 30 (12.8 days from Sep 17).
    """
    remaining = 1156.04
    used = 2000.0 - remaining
    today_spend = 90.02
    # With 9 calendar days left (today + 8), and 90.02 cr/day:
    # 1156.04 / 90.02 = 12.8 days of credits
    # 12.8 > 9, so no alarm should trigger
    pace = compute_pace(
        _account(used=used, limit=2000.0),
        _db(today_spend),
        AppConfig(),
        now=datetime(2026, 9, 17, 16, 48, tzinfo=UTC),
    )
    # Projection should be None since we have more credit-days than calendar days
    assert pace.projection_runout is None


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


def test_available_regions_lists_subdivision_codes(tmp_path: Path) -> None:
    """available_regions returns the sorted distinct subdivision codes."""
    payload = [
        {"date": "2026-01-01", "global": True, "counties": None},
        {"date": "2026-07-09", "global": False, "counties": ["BR-SP"]},
        {"date": "2026-11-20", "global": False, "counties": ["BR-SP", "BR-RJ"]},
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    assert provider.available_regions("BR", 2026) == ["BR-RJ", "BR-SP"]


def test_workday_mode_uses_local_date_not_utc_date(tmp_path: Path) -> None:
    """Workday counting uses the caller's tz for "today," not the UTC date.

    2026-07-17 20:00 UTC is a Friday in UTC, but already Saturday
    2026-07-18 04:00 in Perth (UTC+8) - a working-day count anchored to the
    raw UTC date would wrongly treat "today" as a Friday workday.
    """
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    now = datetime(2026, 7, 17, 20, 0, tzinfo=UTC)
    cfg = AppConfig(workdays=True, country="AU", region="WA")
    account = _account(14.0, 50.0)

    utc_pace = compute_pace(
        account, _db(1.0), cfg, now=now, extras=PaceExtras(holidays=provider)
    )
    perth_pace = compute_pace(
        account,
        _db(1.0),
        cfg,
        now=now,
        extras=PaceExtras(holidays=provider, tz=ZoneInfo("Australia/Perth")),
    )

    assert utc_pace.non_working_today is False  # Friday in UTC
    assert perth_pace.non_working_today is True  # Saturday in Perth
    assert perth_pace.days_gone == utc_pace.days_gone + 1
    assert perth_pace.days_forecast == utc_pace.days_forecast - 1


def test_workday_mode_sets_mode_and_uses_provider(tmp_path: Path) -> None:
    """Workday mode is reflected in PaceInfo.mode."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    cfg = AppConfig(workdays=True, country="US")
    pace = compute_pace(
        _account(14.0, 50.0),
        _db(1.0),
        cfg,
        now=_NOW,
        extras=PaceExtras(holidays=provider),
    )
    assert pace.mode == "workday"
