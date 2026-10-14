"""Render monthly usage as a standalone PDF with vector charts and tables."""

from __future__ import annotations

import calendar
import math
from datetime import timedelta
from html import escape
from io import BytesIO
from typing import TYPE_CHECKING

from reportlab.graphics.shapes import Circle, Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from kiro_meter.pace import billing_cycle_start

if TYPE_CHECKING:
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import BaseDocTemplate

    from kiro_meter.models import AccountInfo
    from kiro_meter.report import ReportData

_INK = colors.HexColor("#172b35")
_MUTED = colors.HexColor("#607079")
_TEAL = colors.HexColor("#147d79")
_LINE = colors.HexColor("#dce4e7")
_PALETTE = ("#147d79", "#496eb0", "#aa681b", "#9061a8", "#bc4b65", "#657535")
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
    spaceBefore=14,
    spaceAfter=8,
    keepWithNext=True,
)
_TITLE = ParagraphStyle("title", parent=_HEADING, fontSize=23, leading=29)
_PAGE_WIDTH, _PAGE_HEIGHT = A4
_MARGIN = 36
_WIDTH = _PAGE_WIDTH - _MARGIN * 2
_CHART_HEIGHT = 160
_GRID_STEPS = 5
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
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
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
    rows = [[_text("Official + estimated", _SMALL), _text("Current reading", _SMALL)]]
    rows.extend([_text(label), _text(value)] for label, value in facts)
    return [
        _text("Current billing-cycle capacity", _HEADING),
        _text(
            f"{start:%d %b %Y, %H:%M %Z} to {account.next_reset:%d %b %Y, %H:%M %Z}",
            _SMALL,
        ),
        Spacer(1, 6),
        _table(rows, [_WIDTH * 0.5, _WIDTH * 0.5]),
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


def _grid(drawing: Drawing, days: int, ceiling: float) -> None:
    """Draw grid lines, credit values, and day-of-month labels."""
    for step in range(_GRID_STEPS + 1):
        y = 25 + step * _CHART_HEIGHT / _GRID_STEPS
        drawing.add(Line(42, y, _WIDTH - 10, y, strokeColor=_LINE))
        drawing.add(
            String(
                36,
                y - 3,
                _number(ceiling * step / _GRID_STEPS),
                textAnchor="end",
                fontSize=7,
                fillColor=_MUTED,
            )
        )
    step = max(1, math.ceil(days / 7))
    for day in sorted({1, days, *range(1, days + 1, step)}):
        x = 42 + (day - 1) * (_WIDTH - 52) / max(days - 1, 1)
        drawing.add(
            String(
                x, 10, f"{day:02d}", textAnchor="middle", fontSize=7, fillColor=_MUTED
            )
        )
    drawing.add(String(42, 198, "Credits / day", fontSize=7, fillColor=_MUTED))


def _line(
    data: ReportData, folder: str, color: colors.Color, ceiling: float
) -> PolyLine:
    """Create a vector line with one point per day, zero-filling missing allocations."""
    points = []
    for day, rows in enumerate(data.daily):
        value = sum(row.credits for row in rows if row.folder == folder)
        x = 42 + day * (_WIDTH - 52) / max(len(data.daily) - 1, 1)
        y = 25 + value / ceiling * _CHART_HEIGHT
        points.extend((x, y))
    return PolyLine(points, strokeColor=color, strokeWidth=1.5, fillColor=None)


def _charts(data: ReportData, labels: dict[str, str]) -> list[Flowable]:
    """Split many projects into charts of at most six lines using a shared scale."""
    maximum = max((row.credits for day in data.daily for row in day), default=0)
    ceiling = maximum * 1.1 if maximum else 1
    charts: list[Flowable] = []
    for offset in range(0, len(data.projects), len(_PALETTE)):
        drawing = Drawing(_WIDTH, 208)
        _grid(drawing, len(data.daily), ceiling)
        legend = []
        for index, row in enumerate(data.projects[offset : offset + len(_PALETTE)]):
            color = colors.HexColor(_PALETTE[index])
            line = _line(data, row.folder, color, ceiling)
            drawing.add(line)
            points = line.points
            for point in range(0, len(points), 2):
                drawing.add(
                    Circle(
                        points[point],
                        points[point + 1],
                        1.8,
                        fillColor=color,
                        strokeColor=None,
                    )
                )
            legend.append(
                Paragraph(
                    f'<font color="{_PALETTE[index]}">'
                    f"<b>{escape(labels[row.folder])}</b></font>",
                    _SMALL,
                )
            )
        charts.extend(
            [
                _text(
                    "Daily credits by project"
                    if offset == 0
                    else "Daily credits by project (continued)",
                    _HEADING,
                ),
                drawing,
                Table([legend], colWidths=[_WIDTH / len(legend)] * len(legend)),
            ]
        )
    return charts


def _models(data: ReportData, labels: dict[str, str]) -> Table:
    """Render model detail with repeating headers for long reports."""
    rows: list[list[object]] = [
        [
            _text(label, _SMALL)
            for label in ("Project", "Recorded model", "Local credits")
        ]
    ]
    rows.extend(
        [_text(labels[row.folder]), _text(row.model), _text(_number(row.credits))]
        for row in data.models
    )
    return _table(rows, [_WIDTH * 0.45, _WIDTH * 0.35, _WIDTH * 0.2])


def _page_footer(canvas: Canvas, _document: BaseDocTemplate) -> None:
    """Stamp page numbers on each page of a potentially long report."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_MUTED)
    canvas.drawString(_MARGIN, 20, "kiro-meter / License usage report")
    canvas.drawRightString(_PAGE_WIDTH - _MARGIN, 20, f"Page {canvas.getPageNumber()}")
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
        *_charts(data, labels),
        _text(
            f"Daily attribution uses session last-update dates in {data.timezone}. "
            "Zero means no retained allocation, not confirmed inactivity. "
            "Today's point, when present, is partial.",
            _SMALL,
        ),
        _text("Project and model detail", _HEADING),
        _models(data, labels),
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
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=f"Kiro license usage / {calendar.month_name[data.month]} {data.year}",
        author="kiro-meter",
    )
    document.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buffer.getvalue()
