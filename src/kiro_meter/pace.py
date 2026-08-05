"""Compute allowance and can-spend pace against the billing cycle."""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING, Protocol

import httpx

from kiro_meter.models import PaceInfo

if TYPE_CHECKING:
    from datetime import datetime, tzinfo
    from pathlib import Path

    from kiro_meter.models import AccountInfo, AppConfig, DbSnapshot, PaceMode

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


@dataclass(frozen=True)
class PaceExtras:
    """Optional inputs to `compute_pace` beyond the core account/db/cfg/now.

    Bundled so `compute_pace` stays within the argument-count limit; these two
    are naturally paired as "extra context the caller may or may not have."
    """

    holidays: HolidayProvider | None = None
    today_baseline: float | None = None
    """The official `used` figure captured at the first run of the local day
    (see `kiro_meter.baseline`). Feeds `since_day_start_per_day`; that field
    is `None` without it."""
    tz: tzinfo = UTC
    """Timezone defining calendar-day boundaries - both modes count whole
    calendar days (never fractional ones), so this decides which "today" is
    being counted. Passing UTC here would silently shift "today" by up to a
    day for users east of it (e.g. AU/WA)."""


_DEFAULT_EXTRAS = PaceExtras()


def compute_pace(
    account: AccountInfo,
    db: DbSnapshot,
    cfg: AppConfig,
    *,
    now: datetime,
    extras: PaceExtras = _DEFAULT_EXTRAS,
) -> PaceInfo:
    """Derive pacing numbers comparing spend against the billing cycle.

    Every day count here is a whole number of calendar (or working) days -
    "today" is always either fully gone, fully in progress, or fully ahead,
    never a fraction of one - so the numbers stay stable for the rest of the
    day instead of drifting every time this is called.

    Args:
        account: Official plan usage (source of limit, used, and reset date).
        db: Local spend snapshot (source of today's credits).
        cfg: Runtime configuration (workday mode, country/region).
        now: The current instant (timezone-aware).
        extras: Optional holiday provider and today's API-usage baseline.

    Returns:
        The computed pacing figures. In calendar mode denominators are calendar
        days; in workday mode they are working days.
    """
    holidays = extras.holidays
    remaining = max(account.limit - account.used, 0.0)
    cycle_start = billing_cycle_start(account.next_reset)
    today_date = now.astimezone(extras.tz).date()
    cycle_start_date = cycle_start.astimezone(extras.tz).date()
    reset_date = account.next_reset.astimezone(extras.tz).date()

    mode: PaceMode
    if cfg.workdays and holidays is not None:
        mode = "workday"
        cycle_len, days_until, non_working, available = _workday_spans(
            cfg,
            holidays,
            today=today_date,
            cycle_start=cycle_start_date,
            reset=reset_date,
        )
    else:
        mode = "calendar"
        cycle_len, days_until = _calendar_spans(
            cycle_start_date, reset_date, today_date
        )
        non_working, available = False, True

    allowance = account.limit / cycle_len if cycle_len >= 1 else None
    today_fraction = db.today_credits / allowance if allowance else None
    runout = account.next_reset if days_until >= 1 else None

    days_gone = max(cycle_len - days_until, 0)
    days_forecast = max(days_until - 1, 0)
    days_into_cycle = min(days_gone + 1, cycle_len) if cycle_len >= 1 else days_gone

    can_spend_credits = (
        days_into_cycle * allowance - account.used if allowance is not None else None
    )
    if_done_today_per_day = remaining / days_forecast if days_forecast >= 1 else None
    since_day_start_per_day = _since_day_start(
        account, extras.today_baseline, days_until
    )

    return PaceInfo(
        mode=mode,
        allowance_per_day=allowance,
        can_spend_credits=can_spend_credits,
        if_done_today_per_day=if_done_today_per_day,
        since_day_start_per_day=since_day_start_per_day,
        days_gone=days_gone,
        days_forecast=days_forecast,
        today_fraction=today_fraction,
        projection_runout=runout,
        non_working_today=non_working,
        holidays_available=available,
    )


def _calendar_spans(cycle_start: date, reset: date, today: date) -> tuple[int, int]:
    """Return (cycle_len, days_until) as whole calendar days.

    Matches `_workday_spans`'s `[today, reset)` semantics: `days_until`
    counts today itself but not the reset date, so a fresh cycle's first
    day has `days_until == cycle_len`.
    """
    return (reset - cycle_start).days, (reset - today).days


def _since_day_start(
    account: AccountInfo, today_baseline: float | None, days_until: int
) -> float | None:
    """Rate for the rest of the cycle (today included) off this morning's baseline.

    Same whole-day denominator `if_done_today_per_day` would use plus one
    (today), but the numerator is frozen to this morning's baseline instead
    of the live `used` total - isolating today's actual spending from the
    rest of the calculation.
    """
    if today_baseline is None or days_until < 1:
        return None
    remaining_from_start = max(account.limit - today_baseline, 0.0)
    return remaining_from_start / days_until


def _workday_spans(
    cfg: AppConfig,
    holidays: HolidayProvider,
    *,
    today: date,
    cycle_start: date,
    reset: date,
) -> tuple[int, int, bool, bool]:
    """Return (working days in cycle, working days until reset, non_working, available).

    `today`/`cycle_start`/`reset` must already be calendar dates in the
    user's own timezone (see `compute_pace`) - public holidays and "today"
    are local-calendar-date concepts, not UTC, so converting instants with
    `.astimezone(tz)` before calling this is what keeps a user east of UTC
    from getting "today" (and the whole working-day count) shifted back by
    up to a day.

    Falls back to a weekends-only count if holidays cannot be fetched.
    """
    country = cfg.country or ""
    try:
        cycle_len = holidays.working_days_between(
            cycle_start, reset, country=country, region=cfg.region
        )
        until = holidays.working_days_between(
            today, reset, country=country, region=cfg.region
        )
        non_working = (
            holidays.working_days_between(
                today, today + _ONE_DAY, country=country, region=cfg.region
            )
            == 0
        )
    except HolidayUnavailableError:
        cycle_len = _weekdays_between(cycle_start, reset)
        until = _weekdays_between(today, reset)
        non_working = today.weekday() >= _FIRST_WEEKEND_DAY
        return cycle_len, until, non_working, False
    return cycle_len, until, non_working, True


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
