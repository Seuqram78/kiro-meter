"""Tests for the local SQLite reader."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kiro_usage.db import build_db_snapshot, load_conversations

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import ConversationSpec

_MS = 1000
_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

_EXPECTED_ONE_CREDIT = 0.02
_EXPECTED_TODAY_TOTAL = 0.10
_EXPECTED_TOP_FOLDER = ("/proj-a", 0.07)
_EXPECTED_BURN = 0.01
_EXPECTED_TURNS = 3
_BURN_WINDOW_MIN = 15


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * _MS)


def test_load_conversations_parses_credits_folder_model(
    make_db: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Credits, folder, and model are extracted per conversation."""
    db = make_db([("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW))])
    rows = load_conversations(db)
    assert len(rows) == 1
    assert rows[0].credits == _EXPECTED_ONE_CREDIT
    assert rows[0].folder == "/proj-a"
    assert rows[0].model_id == "haiku-4.5"


def test_snapshot_aggregates_today_and_breakdowns(
    make_db: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Today's spend, folder, and model breakdowns aggregate correctly."""
    db = make_db(
        [
            ("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW)),
            ("c2", "/proj-b", "sonnet-4.5", 0.03, _ms(_NOW)),
            ("c3", "/proj-a", "haiku-4.5", 0.05, _ms(_NOW)),
        ],
    )
    snap = build_db_snapshot(load_conversations(db), now=_NOW, tz=UTC)
    assert round(snap.today_credits, 2) == _EXPECTED_TODAY_TOTAL
    assert snap.today_turns == _EXPECTED_TURNS
    assert snap.by_folder[0] == _EXPECTED_TOP_FOLDER
    assert snap.approx is True


def test_burn_rate_only_counts_recent(
    make_db: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Burn rate counts only conversations within the window."""
    old = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    db = make_db(
        [
            ("c1", "/p", "m", 0.15, _ms(_NOW)),
            ("c2", "/p", "m", 9.0, _ms(old)),
        ],
    )
    snap = build_db_snapshot(
        load_conversations(db), now=_NOW, tz=UTC, burn_window_min=_BURN_WINDOW_MIN
    )
    assert snap.burn_rate_per_min == _EXPECTED_BURN
