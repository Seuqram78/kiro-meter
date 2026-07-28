"""Compute allowance and can-spend pace against the billing cycle."""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import TYPE_CHECKING, Protocol

import httpx

from kiro_usage.models import PaceInfo

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot, PaceMode

_SECONDS_PER_DAY = 86_400.0
_MIN_DAYS = 0.5
_MONTHS_PER_YEAR = 12
_FIRST_WEEKEND_DAY = 5
_ONE_DAY = timedelta(days=1)
_NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
_HOLIDAY_TIMEOUT_SECONDS = 10.0

Holiday = tuple[date, tuple[str, ...] | None, bool]
"""(date, subdivision codes or None, is-global)."""


class HolidayUnavailableError(Exception):
    """Raised when holidays cannot be fetched and no cache exists."""


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


class NagerHolidayProvider:
    """Working-day source backed by the Nager.Date API with a per-year cache."""

    def __init__(self, client: httpx.Client, cache_dir: Path) -> None:
        """Store the HTTP client and the directory used to cache holidays."""
        self._client = client
        self._cache_dir = cache_dir
        self._memo: dict[tuple[int, str], tuple[Holiday, ...]] = {}

    def working_days_between(
        self,
        start: date,
        end: date,
        *,
        country: str,
        region: str | None,
    ) -> int:
        """Count weekdays in ``[start, end)`` that are not public holidays."""
        count = 0
        day = start
        while day < end:
            if day.weekday() < _FIRST_WEEKEND_DAY and not self._is_holiday(
                day, country, region
            ):
                count += 1
            day += _ONE_DAY
        return count

    def available_regions(self, country: str, year: int) -> list[str]:
        """Return the sorted subdivision codes that have holidays that year."""
        codes: set[str] = set()
        for _holiday_date, counties, _is_global in self._holidays(year, country):
            if counties is not None:
                codes.update(counties)
        return sorted(codes)

    def _is_holiday(self, day: date, country: str, region: str | None) -> bool:
        """Return whether ``day`` is a public holiday for the country/region."""
        for holiday_date, counties, is_global in self._holidays(day.year, country):
            if holiday_date != day:
                continue
            if is_global:
                return True
            if region is not None and counties is not None and region in counties:
                return True
        return False

    def _holidays(self, year: int, country: str) -> tuple[Holiday, ...]:
        """Return cached holidays for a year, fetching and caching on a miss."""
        key = (year, country)
        if key in self._memo:
            return self._memo[key]
        payload = self._load_cached(year, country)
        if payload is None:
            payload = self._fetch(year, country)
            self._store_cache(year, country, payload)
        holidays = _parse_holidays(payload)
        self._memo[key] = holidays
        return holidays

    def _fetch(self, year: int, country: str) -> list[dict[str, object]]:
        """Fetch holidays from Nager.Date, raising if unreachable."""
        url = _NAGER_URL.format(year=year, country=country)
        try:
            response = self._client.get(url, timeout=_HOLIDAY_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            message = f"could not fetch holidays for {country} {year}"
            raise HolidayUnavailableError(message) from exc

    def _cache_path(self, year: int, country: str) -> Path:
        """Path of the on-disk cache file for a country/year."""
        return self._cache_dir / f"holidays-{country}-{year}.json"

    def _load_cached(self, year: int, country: str) -> list[dict[str, object]] | None:
        """Read the cached payload for a year, or None if not cached."""
        path = self._cache_path(year, country)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _store_cache(
        self, year: int, country: str, payload: list[dict[str, object]]
    ) -> None:
        """Persist a fetched payload to the per-year cache file."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(year, country).write_text(
            json.dumps(payload), encoding="utf-8"
        )


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
    cycle_start = billing_cycle_start(account.next_reset)
    calendar_until = _days_between(now, account.next_reset)
    calendar_cycle = _days_between(cycle_start, account.next_reset)
    calendar_elapsed = _days_between(cycle_start, now)

    mode: PaceMode
    if cfg.workdays and holidays is not None:
        mode = "workday"
        cycle_len, days_until, non_working, available = _workday_spans(
            cfg, holidays, now=now, cycle_start=cycle_start, reset=account.next_reset
        )
    else:
        mode = "calendar"
        cycle_len, days_until = calendar_cycle, calendar_until
        non_working, available = False, True

    allowance = account.limit / cycle_len if cycle_len >= _MIN_DAYS else None
    actual = remaining / days_until if days_until >= _MIN_DAYS else None
    today_fraction = db.today_credits / allowance if allowance else None
    runout = now + timedelta(days=days_until) if actual is not None else None

    return PaceInfo(
        mode=mode,
        allowance_per_day=allowance,
        can_spend_per_day=actual,
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
    """Return (working days in cycle, working days until reset, non_working, available).

    Falls back to a weekends-only count if holidays cannot be fetched.
    """
    country = cfg.country or ""
    today = now.date()
    try:
        cycle_len = holidays.working_days_between(
            cycle_start.date(), reset.date(), country=country, region=cfg.region
        )
        until = holidays.working_days_between(
            today, reset.date(), country=country, region=cfg.region
        )
        non_working = (
            holidays.working_days_between(
                today, today + _ONE_DAY, country=country, region=cfg.region
            )
            == 0
        )
    except HolidayUnavailableError:
        cycle_len = _weekdays_between(cycle_start.date(), reset.date())
        until = _weekdays_between(today, reset.date())
        non_working = today.weekday() >= _FIRST_WEEKEND_DAY
        return float(cycle_len), float(until), non_working, False
    return float(cycle_len), float(until), non_working, True


def _weekdays_between(start: date, end: date) -> int:
    """Count Monday to Friday days in ``[start, end)``."""
    count = 0
    day = start
    while day < end:
        if day.weekday() < _FIRST_WEEKEND_DAY:
            count += 1
        day += _ONE_DAY
    return count


def _parse_holidays(payload: list[dict[str, object]]) -> tuple[Holiday, ...]:
    """Convert a Nager.Date payload into Holiday tuples."""
    holidays: list[Holiday] = []
    for item in payload:
        holiday_date = date.fromisoformat(str(item["date"]))
        raw_counties = item.get("counties")
        counties = tuple(raw_counties) if isinstance(raw_counties, list) else None
        holidays.append((holiday_date, counties, bool(item.get("global"))))
    return tuple(holidays)


def billing_cycle_start(reset: datetime) -> datetime:
    """Return the start of the billing cycle that ends at ``reset``.

    The cycle is one calendar month, so its start is the same clock time one
    month before the reset date.
    """
    year = reset.year - 1 if reset.month == 1 else reset.year
    month = _MONTHS_PER_YEAR if reset.month == 1 else reset.month - 1
    last_day = calendar.monthrange(year, month)[1]
    return reset.replace(year=year, month=month, day=min(reset.day, last_day))


def _days_between(start: datetime, end: datetime) -> float:
    """Return the fractional number of days from ``start`` to ``end``."""
    return (end - start).total_seconds() / _SECONDS_PER_DAY
