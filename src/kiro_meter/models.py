"""Shared, immutable data types passed between kiro-meter modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

Provenance = Literal["official", "local", "local_approx", "unavailable"]
"""Where a displayed figure came from and how much to trust it."""

AccountStatus = Literal["ok", "needs_login", "disabled", "error"]
"""Outcome of trying to obtain the official account limit."""

PaceMode = Literal["calendar", "workday"]
"""Whether pacing counts every calendar day or only working days."""


@dataclass(frozen=True)
class ConversationRow:
    """One Kiro conversation's spend, as read from a local session file."""

    conversation_id: str
    folder: str
    model_id: str | None
    credits: float
    updated_at_ms: int


@dataclass(frozen=True)
class DbSnapshot:
    """Aggregated local spend for the current view."""

    today_credits: float
    today_turns: int
    session_credits: float
    session_turns: int
    burn_rate_per_min: float | None
    by_folder_model: tuple[tuple[str, str, int, float], ...]
    recent: tuple[ConversationRow, ...]
    approx: bool


@dataclass(frozen=True)
class AccountInfo:
    """Official plan usage returned by the getUsageLimits endpoint."""

    email: str
    tier: str
    sub_type: str
    used: float
    limit: float
    overage_used: float
    overage_cap: float
    overage_rate: float
    overage_enabled: bool
    next_reset: datetime
    days_until_reset_api: int
    currency: str
    fetched_at: datetime


@dataclass(frozen=True)
class PaceInfo:
    """Derived pacing numbers comparing spend against the billing cycle."""

    mode: PaceMode
    allowance_per_day: float | None
    can_spend_credits: float | None
    if_done_today_per_day: float | None
    since_day_start_per_day: float | None
    days_gone: int
    days_forecast: int
    today_fraction: float | None
    projection_runout: datetime | None
    non_working_today: bool
    holidays_available: bool


@dataclass(frozen=True)
class AppConfig:
    """User-facing runtime configuration."""

    refresh_seconds: int = 30
    use_account: bool = True
    workdays: bool = False
    country: str | None = None
    region: str | None = None
    timezone: str | None = None
    reset_hour: int = 0


@dataclass(frozen=True)
class Snapshot:
    """Everything render needs for one frame."""

    db: DbSnapshot
    account: AccountInfo | None
    account_status: AccountStatus
    pace: PaceInfo | None
    generated_at: datetime
    today_flagged: bool = False
    """Whether the local session-file sum for today exceeds the API-baseline diff."""
