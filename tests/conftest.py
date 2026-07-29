"""Shared pytest fixtures for kiro-meter tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ConversationSpec = tuple[str, str, str, float, int]
"""(conversation_id, folder, model, credits, updated_at_ms)."""


def _iso(updated_at_ms: int) -> str:
    """Render epoch milliseconds as an ISO-8601 UTC timestamp ending in ``Z``."""
    return datetime.fromtimestamp(updated_at_ms / 1000, tz=UTC).isoformat().replace(
        "+00:00", "Z"
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
