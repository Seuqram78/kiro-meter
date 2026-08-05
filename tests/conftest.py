"""Shared pytest fixtures for kiro-meter tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from kiro_meter.models import AccountInfo, DbSnapshot, PaceInfo, UsageRow

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ConversationSpec = tuple[str, str, str, float, int]
"""(conversation_id, folder, model, credits, updated_at_ms)."""

_ACCOUNT_FETCHED_AT = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def db_snapshot() -> DbSnapshot:
    """A representative DbSnapshot shared by render/JSON tests."""
    return DbSnapshot(
        today_credits=0.31,
        today_turns=18,
        session_credits=0.12,
        session_turns=6,
        burn_rate_per_min=0.02,
        by_folder_model=(
            UsageRow("/home/me/proj-a", "sonnet-4.5", 8, 0.21),
            UsageRow("/home/me/proj-b", "haiku-4.5", 12, 0.10),
        ),
        recent=(),
        approx=True,
    )


def account_info() -> AccountInfo:
    """A representative AccountInfo shared by render/JSON tests."""
    return AccountInfo(
        email="u@e.com",
        tier="KIRO FREE",
        sub_type="FREE",
        used=11.21,
        limit=50.0,
        overage_used=0.0,
        overage_cap=10000.0,
        overage_rate=0.04,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=3,
        currency="USD",
        fetched_at=_ACCOUNT_FETCHED_AT,
    )


def pace_info() -> PaceInfo:
    """A representative PaceInfo shared by render/JSON tests."""
    return PaceInfo(
        mode="calendar",
        allowance_per_day=1.61,
        can_spend_credits=11.37,
        if_done_today_per_day=1.72,
        since_day_start_per_day=1.69,
        days_gone=26,
        days_forecast=3,
        today_fraction=0.15,
        projection_runout=None,
        non_working_today=False,
        holidays_available=True,
    )


def _iso(updated_at_ms: int) -> str:
    """Render epoch milliseconds as an ISO-8601 UTC timestamp ending in ``Z``."""
    return (
        datetime.fromtimestamp(updated_at_ms / 1000, tz=UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _session_json(
    session_id: str, cwd: str, model: str, credit: float, updated_at_ms: int
) -> str:
    """Build a Kiro CLI session JSON document with one turn."""
    return json.dumps(
        {
            "session_id": session_id,
            "cwd": cwd,
            "updated_at": _iso(updated_at_ms),
            "session_state": {
                "conversation_metadata": {
                    "user_turn_metadatas": [
                        {
                            "metering_usage": [
                                {"value": credit, "unit": "credit"},
                            ],
                        },
                    ],
                },
                "rts_model_state": {"model_info": {"model_id": model}},
            },
        },
    )


@pytest.fixture
def usage_response() -> dict[str, object]:
    """A sanitized getUsageLimits response body."""
    return {
        "daysUntilReset": 3,
        "nextDateReset": 1785542400.0,
        "overageConfiguration": {"overageLimit": None, "overageStatus": "DISABLED"},
        "subscriptionInfo": {
            "overageCapability": "OVERAGE_INCAPABLE",
            "subscriptionTitle": "KIRO FREE",
            "type": "Q_DEVELOPER_STANDALONE_FREE",
        },
        "usageBreakdownList": [
            {
                "currentUsage": 11,
                "currentUsageWithPrecision": 11.21,
                "usageLimit": 50,
                "usageLimitWithPrecision": 50.0,
                "currentOveragesWithPrecision": 0.0,
                "overageCapWithPrecision": 10000.0,
                "overageRate": 0.04,
                "resourceType": "CREDIT",
                "displayName": "Credit",
                "currency": "USD",
                "freeTrialInfo": None,
            },
        ],
        "userInfo": {"email": "user@example.com"},
    }


@pytest.fixture
def make_sessions(tmp_path: Path) -> Callable[[list[ConversationSpec]], Path]:
    """Return a builder that writes synthetic kiro-cli session files."""

    def build(rows: list[ConversationSpec]) -> Path:
        """Write one session file per spec and return the sessions directory."""
        sessions_dir = tmp_path / "sessions" / "cli"
        sessions_dir.mkdir(parents=True)
        for cid, folder, model, credit, updated in rows:
            blob = _session_json(cid, folder, model, credit, updated)
            (sessions_dir / f"{cid}.json").write_text(blob)
        return sessions_dir

    return build
