"""Tests for the per-day API-usage baseline store."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from kiro_meter.baseline import resolve_daily_baseline

if TYPE_CHECKING:
    from pathlib import Path

_DAY = date(2026, 7, 28)
_OTHER_DAY = date(2026, 7, 29)
_FIRST_USED = 10.0
_HIGHER_USED = 12.5
_NEAR_DUPLICATE_USED = 10.3
_PRE_RESET_USED = 40.0
_POST_RESET_USED = 5.0
_LATER_POST_RESET_USED = 8.0
_LATE_SAME_DAY_USED = 99.0
_OTHER_DAY_USED = 20.0


def test_first_call_captures_used_as_baseline(tmp_path: Path) -> None:
    """The first call for a date returns `used` as the baseline."""
    db_path = tmp_path / "state.sqlite3"
    assert resolve_daily_baseline(db_path, _DAY, _FIRST_USED) == _FIRST_USED


def test_second_call_same_day_returns_original_baseline(tmp_path: Path) -> None:
    """A later, higher `used` doesn't move the baseline for the same day."""
    db_path = tmp_path / "state.sqlite3"
    resolve_daily_baseline(db_path, _DAY, _FIRST_USED)
    assert resolve_daily_baseline(db_path, _DAY, _HIGHER_USED) == _FIRST_USED


def test_lower_used_rebaselines(tmp_path: Path) -> None:
    """A billing-cycle reset (used drops) lowers the baseline to match."""
    db_path = tmp_path / "state.sqlite3"
    resolve_daily_baseline(db_path, _DAY, _PRE_RESET_USED)
    assert resolve_daily_baseline(db_path, _DAY, _POST_RESET_USED) == _POST_RESET_USED
    assert (
        resolve_daily_baseline(db_path, _DAY, _LATER_POST_RESET_USED)
        == _POST_RESET_USED
    )


def test_different_days_get_independent_baselines(tmp_path: Path) -> None:
    """Each local date has its own baseline row."""
    db_path = tmp_path / "state.sqlite3"
    resolve_daily_baseline(db_path, _DAY, _FIRST_USED)
    assert resolve_daily_baseline(db_path, _OTHER_DAY, _OTHER_DAY_USED) == (
        _OTHER_DAY_USED
    )
    assert resolve_daily_baseline(db_path, _DAY, _LATE_SAME_DAY_USED) == _FIRST_USED


def test_concurrent_panes_converge_on_same_baseline(tmp_path: Path) -> None:
    """Two independent callers hitting the same day converge to one baseline."""
    db_path = tmp_path / "state.sqlite3"
    first = resolve_daily_baseline(db_path, _DAY, _FIRST_USED)
    second = resolve_daily_baseline(db_path, _DAY, _NEAR_DUPLICATE_USED)
    assert first == second == _FIRST_USED
