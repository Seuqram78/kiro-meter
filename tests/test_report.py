"""Verify month selection, heatmap attribution, and single-page PDF output."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx
import pytest
from pypdf import PdfReader

from kiro_meter import cli
from kiro_meter.app import RunContext
from kiro_meter.db import load_conversations
from kiro_meter.models import AppConfig, ConversationRow
from kiro_meter.pace import HolidayUnavailableError
from kiro_meter.report import ReportSettings, build_report, run_report
from kiro_meter.report_render import _forecast, _future_day, _heat_band, render_report
from tests.conftest import account_info

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import ConversationSpec

_NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)
_MS = 1000
_SELECTED_CREDITS = 25
_LEAP_DAYS = 21
_TIMEZONE_CREDITS = 3
_PROJECT_LABEL_SECTIONS = 2
_JULY_DAYS = 23
_FIRST_WEEKEND_DAY = 5
_SESSION_CREDITS = 2
_EXIT_CANCELLED = 130
_EXIT_ARGUMENT_ERROR = 2


def _pdf_text(content: bytes) -> str:
    """Parse the PDF and normalize line wraps for content assertions."""
    assert content.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(content))
    assert reader.pages
    return " ".join(" ".join(page.extract_text() for page in reader.pages).split())


def _row(folder: str, amount: float, month: int, day: int) -> ConversationRow:
    """Build a session allocation with a fixed year and UTC timestamp."""
    updated = datetime(2026, month, day, tzinfo=UTC)
    return ConversationRow(folder, "sonnet", amount, int(updated.timestamp() * _MS))


def test_month_nesting_and_zero_filled_heatmap() -> None:
    """Month filtering and nesting preserve the credit sum at daily granularity."""
    rows = [
        _row("/work/api/src", 12, 7, 1),
        _row("/work/api/tests", 8, 7, 3),
        _row("/work/web", 5, 7, 3),
        _row("/work/api", 100, 6, 30),
        _row("/work/api", 100, 7, 25),
    ]
    data = build_report(rows, (2026, 7, 2), now=_NOW, tz=UTC)
    assert [(row.folder, row.credits) for row in data.projects] == [
        ("/work/api", 20),
        ("/work/web", 5),
    ]
    assert len(data.daily) == _JULY_DAYS
    assert not data.daily[1]
    assert sum(row.credits for day in data.daily for row in day) == _SELECTED_CREDITS
    text = _pdf_text(render_report(data))
    assert "Daily credits by project" in text
    assert "25.00 credits" in text
    assert text.count("/work/api") >= _PROJECT_LABEL_SECTIONS
    assert text.count("/work/web") >= _PROJECT_LABEL_SECTIONS
    assert "Historical official allowance" not in text
    assert "Official account data unavailable" in text


def test_historical_leap_month_and_timezone() -> None:
    """Selection uses local month boundaries and includes leap-day zero records."""
    timestamp = datetime(2024, 1, 31, 20, tzinfo=UTC)
    row = ConversationRow("/p", "model", 3, int(timestamp.timestamp() * _MS))
    data = build_report([row], (2024, 2, 1), now=_NOW, tz=ZoneInfo("Australia/Perth"))
    assert len(data.daily) == _LEAP_DAYS
    assert data.daily[0][0].credits == _TIMEZONE_CREDITS
    assert (
        sum(group.credits for day in data.daily for group in day) == _TIMEZONE_CREDITS
    )


def test_escape_labels_and_distinguish_duplicate_names() -> None:
    """PDF escapes path/account markup and omits the removed model detail."""
    rows = [_row("/work/one/api", 1, 7, 1), _row("/work/two/api", 2, 7, 1)]
    rows.append(replace(rows[0], folder='/work/<script>"&', model_id="<img src=x>"))
    account = replace(account_info(), email="<script>alert(1)</script>")
    data = build_report(
        rows,
        (2026, 7, 3),
        now=_NOW,
        tz=UTC,
        settings=ReportSettings(account=account, account_status="ok"),
    )
    text = _pdf_text(render_report(data))
    assert "/work/one/api" in text
    assert "/work/two/api" in text
    assert "<script>alert(1)</script>" in text
    assert "<img src=x>" not in text
    assert "Project and model detail" not in text
    assert "/JavaScript" not in render_report(data).decode("latin-1")


@pytest.mark.parametrize(("used", "limit"), [(0, 0), (0, 50), (50, 50), (60, 50)])
def test_capacity_zero_and_exhausted(used: float, limit: float) -> None:
    """Zero usage/limits never divide by zero; exhausted accounts show status."""
    account = replace(account_info(), used=used, limit=limit, fetched_at=_NOW)
    data = build_report(
        [],
        (2026, 7, 1),
        now=_NOW,
        tz=UTC,
        settings=ReportSettings(account=account, account_status="ok"),
    )
    text = _pdf_text(render_report(data))
    assert "nan" not in text
    assert "Current billing-cycle capacity" in text
    if limit and used >= limit:
        assert "Exhausted" in text


def test_capacity_forecast_uses_official_cycle() -> None:
    """Forecast demand uses official consumption independently of local totals."""
    account = replace(account_info(), used=840, limit=1000, fetched_at=_NOW)
    data = build_report(
        [_row("/p", 10, 7, 1)],
        (2026, 7, 1),
        now=_NOW,
        tz=UTC,
        settings=ReportSettings(account=account, account_status="ok"),
    )
    text = _pdf_text(render_report(data))
    assert "Estimated exhaustion" in text
    assert "1,380.00 credits" in text
    assert "10.00 credits" in text


def test_report_cli_end_to_end(
    make_sessions: Callable[[list[ConversationSpec]], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The requested CLI prompts, retries choices, groups folders, and saves PDF."""
    sessions = make_sessions(
        [
            (
                "one",
                "/work/api/src",
                "sonnet",
                12,
                int(datetime(2025, 1, 1, tzinfo=UTC).timestamp() * _MS),
            ),
            (
                "two",
                "/work/api/tests",
                "haiku",
                8,
                int(datetime(2025, 1, 3, tzinfo=UTC).timestamp() * _MS),
            ),
            (
                "old",
                "/work/old",
                "haiku",
                100,
                int(datetime(2024, 12, 1, tzinfo=UTC).timestamp() * _MS),
            ),
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_SESSIONS_DIR", sessions)
    monkeypatch.setattr(cli, "load_config", lambda: None)
    answers = iter(["invalid", "0", "1", "9", "1", "4", "2"])
    monkeypatch.setattr("builtins.input", lambda *_args: next(answers))
    assert cli.main(["-report", "--no-account"]) == 0
    output = capsys.readouterr().out
    assert "Report year" in output
    assert "2025" in output
    assert "2024" in output
    assert "January" in output
    assert "maximum 3" in output
    files = list(tmp_path.glob("kiro-usage-2025-01-n2-*.pdf"))
    assert len(files) == 1
    text = _pdf_text(files[0].read_bytes())
    assert "20.00 credits" in text
    assert "Historical official allowance" in text
    assert "/work/old" not in text
    assert "/work/api/tests" not in text
    assert "January 2025" in text


def test_report_empty_and_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Missing data and EOF produce useful exit codes and no output artifacts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "DEFAULT_SESSIONS_DIR", tmp_path / "missing")
    monkeypatch.setattr(cli, "load_config", lambda: None)
    assert cli.main(["--report", "--no-account"]) == 1
    sessions = make_sessions([("one", "/p", "model", 1, int(_NOW.timestamp() * _MS))])
    monkeypatch.setattr(cli, "DEFAULT_SESSIONS_DIR", sessions)

    def eof(*_args: object) -> str:
        """Simulate noninteractive stdin."""
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["--report", "--no-account"]) == _EXIT_CANCELLED
    assert not list(tmp_path.glob("*.pdf"))


def test_session_loader_used_by_report(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Real session files supply the same credits to the report and dashboard."""
    sessions = make_sessions([("one", "/p", "model", 2, int(_NOW.timestamp() * _MS))])
    rows = load_conversations(sessions)
    data = build_report(rows, (2026, 7, 1), now=_NOW, tz=UTC)
    assert data.projects[0].credits == _SESSION_CREDITS


def test_many_projects_fit_one_page_without_losing_detail() -> None:
    """Large reports grow the page height and preserve every project."""
    rows = [_row(f"/work/project-{index:02d}", index + 1, 7, 1) for index in range(40)]
    data = build_report(rows, (2026, 7, 2), now=_NOW, tz=UTC)
    content = render_report(data)
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) == 1
    text = _pdf_text(content)
    assert "Daily credits by project (continued)" not in text
    assert all(f"project-{index:02d}" in text for index in range(40))
    assert "820.00 credits" in text


def test_first_day_heatmap_and_long_labels() -> None:
    """A partial month and long paths render on one page with all month columns."""
    folder = "/work/" + "project-name-" * 20
    now = datetime(2026, 7, 1, 12, tzinfo=UTC)
    data = build_report([_row(folder, 1, 7, 1)], (2026, 7, 2), now=now, tz=UTC)
    text = _pdf_text(render_report(data))
    assert "Daily credits by project" in text
    assert "1.00 credits" in text


@pytest.mark.parametrize(
    ("amount", "band"),
    [
        (0, 0),
        (0.01, 1),
        (10, 1),
        (10.01, 2),
        (50, 2),
        (50.01, 3),
        (100, 3),
        (100.01, 4),
        (300, 4),
        (300.01, 5),
        (600, 5),
    ],
)
def test_heatmap_fixed_colour_boundaries(amount: float, band: int) -> None:
    """Small days retain their band even when another day exceeds 600 credits."""
    assert _heat_band(amount) == band


def test_single_page_landscape_and_business_days() -> None:
    """Capacity, allocations, a spike, and business dates remain on one page."""
    rows = [_row("/work/api", 600, 7, 13), _row("/work/web", 0.01, 7, 2)]
    account = replace(account_info(), used=840, limit=1000, fetched_at=_NOW)
    data = build_report(
        rows,
        (2026, 7, 2),
        now=_NOW,
        tz=UTC,
        settings=ReportSettings(account=account, account_status="ok"),
    )
    reader = PdfReader(BytesIO(render_report(data)))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert page.mediabox.width > page.mediabox.height
    text = page.extract_text()
    assert "Current billing-cycle capacity" in text
    assert "Recorded project allocation" in text
    assert "Daily total" in text
    assert "600.00" in text
    assert "0.01" in text
    assert all(f"{day.day:02d}" in text.split() for day in data.dates)
    assert "Recorded model" not in text
    normalized = " ".join(text.split())
    assert "Project / day 01 W 02 T 03 F 06 M 07 T" in normalized


def test_future_dates_use_local_month_not_generation_day() -> None:
    """Historical late-month days remain numeric; current future dates are distinct."""
    data = build_report([], (2026, 6, 1), now=_NOW, tz=UTC)
    assert not _future_day(data, 30)
    current = replace(data, month=7)
    assert not _future_day(current, 20)
    assert _future_day(current, 21)
    near_midnight = replace(
        current,
        generated_at=datetime(2026, 7, 20, 20, tzinfo=UTC),
        timezone=ZoneInfo("Australia/Perth"),
    )
    assert not _future_day(near_midnight, 21)
    assert _future_day(near_midnight, 22)


def test_current_month_survives_missing_token_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Current-month local PDF generation survives an unavailable auth database."""
    sessions = make_sessions(
        [
            ("one", "/p", "model", 2, int(_NOW.timestamp() * _MS)),
        ]
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_args: "1")
    with httpx.Client() as client:
        ctx = RunContext(
            db_path=tmp_path / "missing.sqlite3",
            sessions_dir=sessions,
            client=client,
            tz=UTC,
            holidays=None,
            state_db_path=tmp_path / "state.sqlite3",
        )
        assert run_report(AppConfig(), ctx, now=_NOW) == 0
    files = list(tmp_path.glob("*.pdf"))
    assert len(files) == 1
    text = _pdf_text(files[0].read_bytes())
    assert "Official account lookup failed" in text
    assert "2.00 credits" in text


@pytest.mark.parametrize("flag", ["--once", "--json", "--reconfigure"])
def test_report_rejects_conflicting_modes(flag: str) -> None:
    """Report mode never consumes setup or snapshot prompts."""
    with pytest.raises(SystemExit) as error:
        cli.main(["-report", flag])
    assert error.value.code == _EXIT_ARGUMENT_ERROR


class _ReportHolidays:
    """Holiday fixture recording the country and region requested by the report."""

    def __init__(self, excluded: set[date], *, unavailable: bool = False) -> None:
        """Keep known holidays and optionally simulate a provider outage."""
        self.excluded = excluded
        self.unavailable = unavailable
        self.calls: list[tuple[str, str | None]] = []

    def working_days_between(
        self, start: date, end: date, *, country: str, region: str | None
    ) -> int:
        """Count weekdays excluding the fixture's public holidays."""
        self.calls.append((country, region))
        if self.unavailable:
            raise HolidayUnavailableError
        days = (start + timedelta(days=offset) for offset in range((end - start).days))
        return sum(
            day.weekday() < _FIRST_WEEKEND_DAY and day not in self.excluded
            for day in days
        )


def test_business_forecast_skips_weekends_and_holidays() -> None:
    """Friday's capacity advances over business dates and regional holidays."""
    now = datetime(2026, 7, 3, 12, tzinfo=UTC)
    account = replace(account_info(), used=120, limit=240, fetched_at=now)
    holidays = _ReportHolidays({date(2026, 7, 6)})
    settings = ReportSettings(account, "ok", "AU", "AU-WA", holidays)
    rows = [
        _row("/work/api", 2, 7, 7),
        _row("/work/api", 5, 7, 4),
        _row("/work/api", 10, 7, 6),
    ]
    data = build_report(rows, (2026, 7, 2), now=now, tz=UTC, settings=settings)
    facts = dict(_forecast(account, data))
    assert facts["Estimated exhaustion"] == "09 Jul 2026"
    assert facts["Before reset"] == "16 business days"
    assert date(2026, 7, 4) not in data.dates
    assert date(2026, 7, 6) not in data.dates
    assert holidays.calls
    assert set(holidays.calls) == {("AU", "AU-WA")}
    assert "AU/AU-WA public holidays excluded" in data.calendar.coverage


def test_business_allocations_exclude_weekend_and_holiday_credits() -> None:
    """Local totals and heatmap contain the same business-day allocations."""
    holidays = _ReportHolidays({date(2026, 7, 6)})
    rows = [
        _row("/work/api", 2, 7, 7),
        _row("/work/api", 5, 7, 4),
        _row("/work/api", 10, 7, 6),
    ]
    settings = ReportSettings(country="AU", region="AU-WA", holidays=holidays)
    data = build_report(rows, (2026, 7, 2), now=_NOW, tz=UTC, settings=settings)
    assert sum(row.credits for row in data.projects) == _SESSION_CREDITS
    assert sum(row.credits for day in data.daily for row in day) == _SESSION_CREDITS
    assert all(day.weekday() < _FIRST_WEEKEND_DAY for day in data.dates)


def test_holiday_outage_falls_back_consistently() -> None:
    """Failed holiday lookup labels a Monday-Friday fallback throughout the report."""
    holidays = _ReportHolidays(set(), unavailable=True)
    settings = ReportSettings(country="AU", holidays=holidays)
    data = build_report(
        [_row("/work/api", 2, 7, 6)], (2026, 7, 2), now=_NOW, tz=UTC, settings=settings
    )
    assert date(2026, 7, 6) in data.dates
    assert all(day.weekday() < _FIRST_WEEKEND_DAY for day in data.dates)
    assert "public holidays unavailable" in _pdf_text(render_report(data))


def test_report_grows_without_scaling_text() -> None:
    """More projects increase page height while preserving text's drawing scale."""
    rows = [_row(f"/work/project-{index:02d}", index + 1, 7, 1) for index in range(40)]
    short = build_report(rows[:1], (2026, 7, 2), now=_NOW, tz=UTC)
    long = build_report(rows, (2026, 7, 2), now=_NOW, tz=UTC)
    short_page = PdfReader(BytesIO(render_report(short))).pages[0]
    long_reader = PdfReader(BytesIO(render_report(long)))
    assert len(long_reader.pages) == 1
    long_page = long_reader.pages[0]
    assert long_page.mediabox.height > short_page.mediabox.height
    assert long_page.mediabox.width == short_page.mediabox.width
    scales: list[tuple[float, float]] = []

    def capture(
        text: str,
        matrix: list[float],
        _text_matrix: list[float],
        _font: object,
        _font_size: float,
    ) -> None:
        """Capture project labels' actual PDF transformation matrices."""
        if "/work/project-" in text:
            scales.append((matrix[0], matrix[3]))

    long_page.extract_text(visitor_text=capture)
    assert scales
    assert set(scales) == {(1.0, 1.0)}


def test_report_width_follows_business_date_count() -> None:
    """Different monthly business calendars produce different page widths."""
    july = build_report([], (2026, 7, 1), now=_NOW, tz=UTC)
    february = build_report([], (2024, 2, 1), now=_NOW, tz=UTC)
    july_page = PdfReader(BytesIO(render_report(july))).pages[0]
    february_page = PdfReader(BytesIO(render_report(february))).pages[0]
    assert july_page.mediabox.width > february_page.mediabox.width
