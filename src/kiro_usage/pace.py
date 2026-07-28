"""Compute target and actual pace against the billing cycle."""

from __future__ import annotations

import calendar
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from kiro_usage.models import PaceInfo

if TYPE_CHECKING:
    from datetime import date, datetime

    from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot, PaceMode

_SECONDS_PER_DAY = 86_400.0
_MIN_DAYS = 0.5
_MONTHS_PER_YEAR = 12
_ONE_DAY = timedelta(days=1)


class HolidayProvider(Protocol):
    """Source of working-day counts for a country/region."""

    def working_days_between(
        self,
        start: date,
        end: date,
        *,
        country: str,
        region: str | None,
    ) -> int:
        """Count working days (weekdays minus holidays) in ``[start, end)``."""
        ...


def compute_pace(
    account: AccountInfo,
    db: DbSnapshot,
    cfg: AppConfig,
    *,
    now: datetime,
    holidays: HolidayProvider | None = None,
) -> PaceInfo:
    """Derive pacing numbers comparing spend against the billing cycle.

    Args:
        account: Official plan usage (source of limit, used, and reset date).
        db: Local spend snapshot (source of today's credits).
        cfg: Runtime configuration (workday mode, country/region).
        now: The current instant (timezone-aware).
        holidays: Working-day source; required for workday mode.

    Returns:
        The computed pacing figures. In calendar mode denominators are calendar
        days; in workday mode they are working days.
    """
    remaining = max(account.limit - account.used, 0.0)
    cycle_start = _month_before(account.next_reset)
    calendar_until = _days_between(now, account.next_reset)
    calendar_elapsed = _days_between(cycle_start, now)

    mode: PaceMode
    if cfg.workdays and holidays is not None:
        mode = "workday"
        days_until, days_elapsed, non_working, available = _workday_spans(
            cfg, holidays, now=now, cycle_start=cycle_start, reset=account.next_reset
        )
    else:
        mode = "calendar"
        days_until, days_elapsed = calendar_until, calendar_elapsed
        non_working, available = False, True

    target = remaining / days_until if days_until >= _MIN_DAYS else None
    actual = account.used / days_elapsed if days_elapsed >= _MIN_DAYS else None
    today_fraction = db.today_credits / target if target else None
    runout = now + timedelta(days=remaining / actual) if actual else None

    return PaceInfo(
        mode=mode,
        target_per_day=target,
        actual_per_day=actual,
        today_fraction=today_fraction,
        days_until_reset=calendar_until,
        days_elapsed=calendar_elapsed,
        projection_runout=runout,
        non_working_today=non_working,
        holidays_available=available,
    )


def _workday_spans(
    cfg: AppConfig,
    holidays: HolidayProvider,
    *,
    now: datetime,
    cycle_start: datetime,
    reset: datetime,
) -> tuple[float, float, bool, bool]:
    """Return (working days until reset, elapsed, non_working_today, available)."""
    country = cfg.country or ""
    today = now.date()
    until = holidays.working_days_between(
        today, reset.date(), country=country, region=cfg.region
    )
    elapsed = holidays.working_days_between(
        cycle_start.date(), today, country=country, region=cfg.region
    )
    non_working = (
        holidays.working_days_between(
            today, today + _ONE_DAY, country=country, region=cfg.region
        )
        == 0
    )
    return float(until), float(elapsed), non_working, True


def _month_before(dt: datetime) -> datetime:
    """Return the same clock time one calendar month earlier."""
    year = dt.year - 1 if dt.month == 1 else dt.year
    month = _MONTHS_PER_YEAR if dt.month == 1 else dt.month - 1
    last_day = calendar.monthrange(year, month)[1]
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


def _days_between(start: datetime, end: datetime) -> float:
    """Return the fractional number of days from ``start`` to ``end``."""
    return (end - start).total_seconds() / _SECONDS_PER_DAY
