"""Build a consistent business-day calendar using the dashboard holiday provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from kiro_meter.pace import HolidayUnavailableError

if TYPE_CHECKING:
    from datetime import date

    from kiro_meter.pace import HolidayProvider

_FIRST_WEEKEND_DAY = 5


@dataclass(frozen=True)
class BusinessCalendar:
    """Working dates and the holiday coverage used throughout a report."""

    days: tuple[date, ...]
    coverage: str


def business_calendar(
    start: date,
    end: date,
    country: str | None,
    region: str | None,
    holidays: HolidayProvider | None,
) -> BusinessCalendar:
    """Exclude weekends and holidays, falling back consistently on failure."""
    weekdays = tuple(
        day
        for offset in range((end - start).days)
        if (day := start + timedelta(days=offset)).weekday() < _FIRST_WEEKEND_DAY
    )
    if not country or holidays is None:
        return BusinessCalendar(
            weekdays, "Monday-Friday; public holidays not configured"
        )
    try:
        days = tuple(
            day
            for day in weekdays
            if holidays.working_days_between(
                day,
                day + timedelta(days=1),
                country=country,
                region=region,
            )
            > 0
        )
    except HolidayUnavailableError:
        return BusinessCalendar(weekdays, "Monday-Friday; public holidays unavailable")
    location = f"{country}/{region}" if region else country
    return BusinessCalendar(days, f"Monday-Friday; {location} public holidays excluded")
