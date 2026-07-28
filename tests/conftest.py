"""Shared pytest fixtures for kiro-usage tests."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ConversationSpec = tuple[str, str, str, float, int]
"""(conversation_id, folder, model, credits, updated_at_ms)."""


def _conversation_json(cwd: str, model: str, credit: float) -> str:
    """Build a Kiro conversation JSON blob with one turn."""
    return json.dumps(
        {
            "history": [
                {
                    "user": {
                        "env_context": {
                            "env_state": {"current_working_directory": cwd},
                        },
                    },
                    "request_metadata": {"model_id": model},
                },
            ],
            "user_turn_metadata": {
                "usage_info": [{"value": credit, "unit": "credit"}],
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
def make_db(tmp_path: Path) -> Callable[[list[ConversationSpec]], Path]:
    """Return a builder that writes a synthetic kiro-cli SQLite database."""

    def build(rows: list[ConversationSpec]) -> Path:
        """Create the database file from conversation specs and return its path."""
        db_path = tmp_path / "data.sqlite3"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE conversations_v2 (key TEXT, conversation_id TEXT, "
                "value TEXT, created_at INTEGER, updated_at INTEGER)",
            )
            for cid, folder, model, credit, updated in rows:
                blob = _conversation_json(folder, model, credit)
                conn.execute(
                    "INSERT INTO conversations_v2 VALUES (?, ?, ?, ?, ?)",
                    (folder, cid, blob, updated, updated),
                )
            conn.commit()
        finally:
            conn.close()
        return db_path

    return build
