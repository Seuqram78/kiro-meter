"""Tests for the local session reader."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kiro_meter.db import build_db_snapshot, load_conversations

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.conftest import ConversationSpec

_MS = 1000
_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

_EXPECTED_ONE_CREDIT = 0.02
_EXPECTED_TODAY_TOTAL = 0.10
_EXPECTED_TOP_GROUP = ("/proj-a", "haiku-4.5", 2)
_EXPECTED_TOP_CREDITS = 0.07
_EXPECTED_BURN = 0.01
_EXPECTED_TURNS = 3
_BURN_WINDOW_MIN = 15
_NDIGITS = 2
_EXPECTED_AUTO_MODEL_CREDIT = 0.04


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * _MS)


def test_load_conversations_parses_credits_folder_model(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Credits, folder, and model are extracted per conversation."""
    sessions_dir = make_sessions([("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW))])
    rows = load_conversations(sessions_dir)
    assert len(rows) == 1
    assert rows[0].credits == _EXPECTED_ONE_CREDIT
    assert rows[0].folder == "/proj-a"
    assert rows[0].model_id == "haiku-4.5"


def test_load_conversations_handles_null_model_info(tmp_path: Path) -> None:
    """A session with ``model_info: null`` (the "auto" model) doesn't crash the read."""
    sessions_dir = tmp_path / "sessions" / "cli"
    sessions_dir.mkdir(parents=True)
    session = {
        "session_id": "auto-session",
        "cwd": "/proj-auto",
        "updated_at": "2026-07-28T12:00:00.000000Z",
        "session_state": {
            "conversation_metadata": {
                "user_turn_metadatas": [
                    {"metering_usage": [{"value": 0.04, "unit": "credit"}]},
                ],
            },
            "rts_model_state": {"model_info": None},
        },
    }
    (sessions_dir / "auto-session.json").write_text(json.dumps(session))

    rows = load_conversations(sessions_dir)

    assert len(rows) == 1
    assert rows[0].model_id is None
    assert rows[0].credits == _EXPECTED_AUTO_MODEL_CREDIT


def test_by_folder_model_includes_more_than_five_groups(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Every folder/model group is kept, not just the top 5."""
    specs = [
        (f"c{i}", f"/proj-{i}", "haiku", 0.01 * i, _ms(_NOW)) for i in range(1, 8)
    ]
    sessions_dir = make_sessions(specs)
    snap = build_db_snapshot(load_conversations(sessions_dir), now=_NOW, tz=UTC)
    assert len(snap.by_folder_model) == len(specs)


def test_snapshot_aggregates_today_and_breakdowns(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Today's spend, folder, and model breakdowns aggregate correctly."""
    sessions_dir = make_sessions(
        [
            ("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW)),
            ("c2", "/proj-b", "sonnet-4.5", 0.03, _ms(_NOW)),
            ("c3", "/proj-a", "haiku-4.5", 0.05, _ms(_NOW)),
        ],
    )
    snap = build_db_snapshot(load_conversations(sessions_dir), now=_NOW, tz=UTC)
    assert round(snap.today_credits, _NDIGITS) == _EXPECTED_TODAY_TOTAL
    assert snap.today_turns == _EXPECTED_TURNS
    top = snap.by_folder_model[0]
    assert top[:3] == _EXPECTED_TOP_GROUP
    assert round(top[3], _NDIGITS) == _EXPECTED_TOP_CREDITS
    assert snap.approx is True


def test_usage_scoped_to_cycle_with_since(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """`since` limits the folder/model usage to the current cycle."""
    prev_cycle = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    this_cycle = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    cutoff = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    sessions_dir = make_sessions(
        [
            ("old", "/proj-a", "haiku", 9.0, _ms(prev_cycle)),
            ("new", "/proj-b", "haiku", 0.05, _ms(this_cycle)),
        ],
    )
    snap = build_db_snapshot(
        load_conversations(sessions_dir), now=_NOW, tz=UTC, since=cutoff
    )
    assert len(snap.by_folder_model) == 1
    assert snap.by_folder_model[0][0] == "/proj-b"


def test_burn_rate_only_counts_recent(
    make_sessions: Callable[[list[ConversationSpec]], Path],
) -> None:
    """Burn rate counts only conversations within the window."""
    old = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    sessions_dir = make_sessions(
        [
            ("c1", "/p", "m", 0.15, _ms(_NOW)),
            ("c2", "/p", "m", 9.0, _ms(old)),
        ],
    )
    snap = build_db_snapshot(
        load_conversations(sessions_dir),
        now=_NOW,
        tz=UTC,
        burn_window_min=_BURN_WINDOW_MIN,
    )
    assert snap.burn_rate_per_min == _EXPECTED_BURN
