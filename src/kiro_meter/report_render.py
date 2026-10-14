"""Render monthly usage as a standalone PDF with vector charts and tables."""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from html import escape
from io import BytesIO
from typing import TYPE_CHECKING

from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from kiro_meter.pace import billing_cycle_start

if TYPE_CHECKING:
    from kiro_meter.models import AccountInfo
    from kiro_meter.report import ReportData

_INK = colors.HexColor("#172b35")
_MUTED = colors.HexColor("#607079")
_TEAL = colors.HexColor("#147d79")
_LINE = colors.HexColor("#dce4e7")
_HEAT_LIMITS = (0, 10, 50, 100, 300, float("inf"))
_HEAT_COLORS = ("#edf2f2", "#e0f0e9", "#b5dccb", "#75b99e", "#348866", "#18543e")
_HEAT_LABELS = ("0", ">0-10", ">10-50", ">50-100", ">100-300", ">300")
_METRIC_COLUMNS = 5
_MEASURE_HEIGHT = 1_000_000
_HEAT_PATH_WIDTH = 190
_HEAT_DARK_BAND = 4
_HEAT_TEXT = ParagraphStyle(
    "heat", fontName="Helvetica", fontSize=7, leading=9, alignment=1
)
_BODY = ParagraphStyle(
    "body", fontName="Helvetica", fontSize=9, leading=13, textColor=_INK, wordWrap="CJK"
)
_SMALL = ParagraphStyle("small", parent=_BODY, fontSize=7, leading=10, textColor=_MUTED)
_HEADING = ParagraphStyle(
    "heading",
    parent=_BODY,
    fontName="Helvetica-Bold",
    fontSize=12,
    leading=16,
    spaceBefore=7,
    spaceAfter=6,
    keepWithNext=True,
)
_TITLE = ParagraphStyle("title", parent=_HEADING, fontSize=23, leading=29)
_PAGE_SIZE = (1280, 720)
_PAGE_WIDTH, _PAGE_HEIGHT = _PAGE_SIZE
_MARGIN = 18
_WIDTH = _PAGE_WIDTH - _MARGIN * 2
_METRIC_VALUE = ParagraphStyle(
    "metric-value", parent=_BODY, fontName="Helvetica-Bold", fontSize=12, leading=16
)
_MIN_FORECAST_DAYS = 0.5
_SECONDS_PER_DAY = 86400


def _number(value: float) -> str:
    """Format credits without hiding small nonzero values."""
    return f"{value:,.2f}"


def _text(text: str, style: ParagraphStyle = _BODY) -> Paragraph:
    """Render dynamic text without interpreting it as paragraph markup."""
    return Paragraph(escape(text), style)


def _labels(data: ReportData) -> dict[str, str]:
    """Show each project's folder path at the selected nesting level."""
    return {row.folder: row.folder for row in data.projects}


def _table(rows: list[list[object]], widths: list[float]) -> Table:
    """Create a table that repeats column labels when it spans pages."""
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f5")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, _LINE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, _LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _forecast(account: AccountInfo) -> list[tuple[str, str]]:
    """Generate capacity facts from official usage and calendar-day cycle pacing."""
    start = billing_cycle_start(account.next_reset)
    elapsed = (account.fetched_at - start).total_seconds() / _SECONDS_PER_DAY
    duration = (account.next_reset - start).total_seconds() / _SECONDS_PER_DAY
    facts = [("Calendar cycle elapsed", f"{elapsed / duration:.1%}")]
    if account.used >= account.limit and account.limit > 0:
        return [*facts, ("Allowance status", "Exhausted")]
    if elapsed < _MIN_FORECAST_DAYS or account.used <= 0 or account.limit <= 0:
        return [*facts, ("Forecast", "Insufficient data")]
    average = account.used / elapsed
    projected = average * duration
    facts.append(("Projected cycle demand", f"{_number(projected)} credits"))
    if projected <= account.limit:
        return [*facts, ("Current pace", "Within allowance")]
    exhaustion = account.fetched_at + timedelta(
        days=(account.limit - account.used) / average
    )
    gap = (account.next_reset - exhaustion).total_seconds() / _SECONDS_PER_DAY
    return [
        *facts,
        ("Estimated exhaustion", f"{exhaustion:%d %b %Y}"),
        ("Before reset", f"~{gap:.1f} days"),
    ]


def _capacity(data: ReportData) -> list[Flowable]:
    """Keep official current-cycle capacity separate from selected-month allocations."""
    account = data.account
    if account is None:
        reason = {
            "historical": (
                "Historical official allowance and account totals are unavailable."
            ),
            "needs_login": (
                "Official account data unavailable. Sign in through kiro-cli."
            ),
            "disabled": "Official account lookup disabled.",
            "error": "Official account lookup failed.",
        }.get(data.account_status, "Official account data unavailable.")
        return [_text("Account capacity", _HEADING), _text(reason, _SMALL)]
    start = billing_cycle_start(account.next_reset)
    consumed = (
        f"{account.used / account.limit:.1%}" if account.limit > 0 else "Unavailable"
    )
    facts = [
        ("Allowance consumed", consumed),
        (
            "Official credits / allowance",
            f"{_number(account.used)} / {_number(account.limit)} credits",
        ),
        ("Credits remaining", _number(max(account.limit - account.used, 0))),
        ("Overage consumed", f"{_number(account.overage_used)} credits"),
        ("Overage status", "Enabled" if account.overage_enabled else "Disabled"),
        *_forecast(account),
    ]
    cells = [
        [_text(label, _SMALL), _text(value, _METRIC_VALUE)] for label, value in facts
    ]
    cells.extend([[] for _ in range((-len(cells)) % _METRIC_COLUMNS)])
    metric_rows = [
        cells[index : index + _METRIC_COLUMNS]
        for index in range(0, len(cells), _METRIC_COLUMNS)
    ]
    metrics = Table(metric_rows, colWidths=[_WIDTH / _METRIC_COLUMNS] * _METRIC_COLUMNS)
    metrics.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [
        _text("Current billing-cycle capacity", _HEADING),
        _text(
            f"{start:%d %b %Y, %H:%M %Z} to {account.next_reset:%d %b %Y, %H:%M %Z}",
            _SMALL,
        ),
        Spacer(1, 6),
        metrics,
        Spacer(1, 5),
        _text(
            "Forecast: average official credits per elapsed calendar day; "
            "assumes unchanged demand. Billing-cycle dates may differ "
            "from the selected calendar month.",
            _SMALL,
        ),
    ]


def _allocation(data: ReportData, labels: dict[str, str]) -> Table:
    """Render every folder group with credits, local share, and a proportional bar."""
    total = sum(row.credits for row in data.projects)
    rows: list[list[object]] = [
        [
            _text(label, _SMALL)
            for label in ("Project", "Local credits", "Local share", "Allocation")
        ]
    ]
    for row in data.projects:
        share = row.credits / total if total else 0
        bar = Drawing(100, 12)
        bar.add(Rect(0, 3, 100, 6, fillColor=_LINE, strokeColor=None))
        bar.add(Rect(0, 3, 100 * share, 6, fillColor=_TEAL, strokeColor=None))
        rows.append(
            [
                _text(labels[row.folder]),
                _text(_number(row.credits)),
                _text(f"{share:.1%}"),
                bar,
            ]
        )
    rows.append(
        [
            _text("Total"),
            _text(_number(total)),
            _text("100.0%" if total else "0.0%"),
            "",
        ]
    )
    return _table(rows, [_WIDTH * 0.42, _WIDTH * 0.18, _WIDTH * 0.17, _WIDTH * 0.23])


def _heat_band(value: float) -> int:
    """Map credits to fixed bands, independent of other days' consumption."""
    return next(index for index, limit in enumerate(_HEAT_LIMITS) if value <= limit)


def _heat_cell(value: float) -> Paragraph:
    """Print exact credits with a foreground suited to the cell's colour."""
    band = _heat_band(value)
    foreground = "#ffffff" if band >= _HEAT_DARK_BAND else "#172b35"
    return Paragraph(f'<font color="{foreground}">{_number(value)}</font>', _HEAT_TEXT)


def _heat_date_cell(
    data: ReportData, day: int, value: float
) -> tuple[Paragraph, colors.Color]:
    """Leave future dates distinct from measured zero-credit allocations."""
    if _future_day(data, day):
        return _text("—", _HEAT_TEXT), colors.HexColor("#f7f9f9")
    return _heat_cell(value), colors.HexColor(_HEAT_COLORS[_heat_band(value)])


def _heat_row(
    data: ReportData, label: str, folder: str | None
) -> tuple[list[object], list[colors.Color]]:
    """Build one project row, or the daily totals when folder is absent."""
    cells: list[object] = [_text(label, _SMALL)]
    backgrounds = []
    for day, allocations in enumerate(data.daily, start=1):
        value = sum(
            row.credits for row in allocations if folder is None or row.folder == folder
        )
        cell, color = _heat_date_cell(data, day, value)
        cells.append(cell)
        backgrounds.append(color)
    return cells, backgrounds


def _heat_table(data: ReportData, labels: dict[str, str]) -> Table:
    """Render all project rows and month columns in one heatmap table."""
    header: list[object] = [_text("Project / day", _SMALL)]
    header.extend(
        [
            _text(f"{day:02d}", _HEAT_TEXT),
            _text(
                calendar.day_name[date(data.year, data.month, day).weekday()][0],
                _HEAT_TEXT,
            ),
        ]
        for day in range(1, len(data.daily) + 1)
    )
    heat_rows = [
        _heat_row(data, labels[row.folder], row.folder) for row in data.projects
    ]
    heat_rows.append(_heat_row(data, "Daily total", None))
    rows = [header, *[cells for cells, _ in heat_rows]]
    commands = [
        ("BACKGROUND", (column, row), (column, row), color)
        for row, (_, backgrounds) in enumerate(heat_rows, start=1)
        for column, color in enumerate(backgrounds, start=1)
    ]
    day_width = (_WIDTH - _HEAT_PATH_WIDTH) / len(data.daily)
    table = _table(rows, [_HEAT_PATH_WIDTH, *([day_width] * len(data.daily))])
    table.setStyle(
        TableStyle(
            [
                *commands,
                ("LEFTPADDING", (1, 0), (-1, -1), 1),
                ("RIGHTPADDING", (1, 0), (-1, -1), 1),
                ("VALIGN", (1, 0), (-1, -1), "MIDDLE"),
                ("GRID", (1, 1), (-1, -1), 0.6, colors.white),
            ]
        )
    )
    return table


def _heat_legend() -> Table:
    """Explain fixed colour intervals, including fractional positive usage."""
    cells = [
        Paragraph(
            f'<font color="{"#ffffff" if index >= _HEAT_DARK_BAND else "#172b35"}">'
            f"{label}</font>",
            _HEAT_TEXT,
        )
        for index, label in enumerate(_HEAT_LABELS)
    ]
    table = Table([cells], colWidths=[_WIDTH / len(cells)] * len(cells))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (index, 0), (index, 0), colors.HexColor(color))
                for index, color in enumerate(_HEAT_COLORS)
            ]
        )
    )
    return table


def _future_day(data: ReportData, day: int) -> bool:
    """Distinguish future dates from zero recorded usage, using the report timezone."""
    return (
        date(data.year, data.month, day)
        > data.generated_at.astimezone(data.timezone).date()
    )


def _heatmap(data: ReportData, labels: dict[str, str]) -> list[Flowable]:
    """Render the entire month in one table, using fixed colour bands."""
    return [
        _text("Daily credits by project", _HEADING),
        _text(
            f"1-{len(data.daily)} {calendar.month_name[data.month]} {data.year}", _SMALL
        ),
        Spacer(1, 5),
        _heat_table(data, labels),
        Spacer(1, 8),
        _heat_legend(),
        _text("Credits per cell / fixed colour bands / — = future date", _SMALL),
    ]


def _page_footer(canvas: Canvas) -> None:
    """Stamp the report footer outside the scaled content area."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_MARGIN, 8, "kiro-meter / License usage report")
    canvas.drawRightString(_PAGE_WIDTH - _MARGIN, 8, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def render_report(data: ReportData) -> bytes:
    """Build a PDF in memory, with no browser, HTML files, or network assets."""
    labels = _labels(data)
    local_now = data.generated_at.astimezone(data.timezone)
    current = (data.year, data.month) == (local_now.year, local_now.month)
    scope = "Month to date" if current else "Retained month records"
    identity = data.account.email if data.account else "Local CLI usage"
    tier = data.account.tier if data.account else "Official plan unavailable"
    total = sum(row.credits for row in data.projects)
    story: list[Flowable] = [
        _text("KIRO / LICENSE REVIEW", _SMALL),
        _text("License usage report", _TITLE),
        _text(f"{calendar.month_name[data.month]} {data.year} / {scope}"),
        _text(f"{identity} / {tier} / Nesting level {data.nesting}", _SMALL),
        *_capacity(data),
        _text("Recorded project allocation", _HEADING),
        _text(
            f"{_number(total)} credits / {len(data.projects)} folder groups / "
            "Local, approximate",
            _SMALL,
        ),
        Spacer(1, 6),
        _allocation(data, labels),
        _text(
            f"Project = working directory collapsed to nesting level {data.nesting}, "
            "counted from the filesystem root. Shares use the local monthly total.",
            _SMALL,
        ),
        *_heatmap(data, labels),
        _text(
            f"Daily attribution uses session last-update dates in {data.timezone}. "
            "Zero means no retained allocation, not confirmed inactivity. "
            "Today's column, when present, is partial.",
            _SMALL,
        ),
        _text("Data coverage", _HEADING),
        _text(
            "Retained local CLI sessions only; IDE activity is excluded. "
            "Cumulative session credits follow the last update date "
            "and recorded model. "
            "Sessions spanning months or switching models can distort daily/monthly "
            "allocations. Historical official totals are unavailable; local "
            "allocations need not match official consumption.",
            _SMALL,
        ),
        Spacer(1, 6),
        _text(
            f"Generated {local_now:%d %b %Y, %H:%M %Z} / Timezone: {data.timezone}",
            _SMALL,
        ),
    ]
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=_PAGE_SIZE)
    canvas.setTitle(
        f"Kiro license usage / {calendar.month_name[data.month]} {data.year}"
    )
    canvas.setAuthor("kiro-meter")
    _draw_single_page(canvas, story)
    _page_footer(canvas)
    canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _draw_single_page(canvas: Canvas, story: list[Flowable]) -> None:
    """Measure the full report and scale it uniformly to fit one landscape page."""
    measured = [
        (item, item.wrapOn(canvas, _WIDTH, _MEASURE_HEIGHT)[1]) for item in story
    ]
    height = sum(
        item.getSpaceBefore() + size + item.getSpaceAfter() for item, size in measured
    )
    available = _PAGE_HEIGHT - _MARGIN * 2
    scale = min(1.0, available / height)
    canvas.saveState()
    canvas.translate(_MARGIN + _WIDTH * (1 - scale) / 2, _MARGIN)
    canvas.scale(scale, scale)
    y = available / scale
    for item, size in measured:
        y -= item.getSpaceBefore() + size
        item.drawOn(canvas, 0, y)
        y -= item.getSpaceAfter()
    canvas.restoreState()
