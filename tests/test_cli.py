"""Smoke tests for the package and the one-shot runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from kiro_meter import __version__
from kiro_meter.app import RunContext, run_once
from kiro_meter.models import AppConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

    from tests.conftest import ConversationSpec

_MS = 1000
_EXIT_INDETERMINATE = 20


def test_version_is_a_string() -> None:
    """The package exposes a string version."""
    assert isinstance(__version__, str)
    assert __version__


def test_run_once_json_local_only(
    make_sessions: Callable[[list[ConversationSpec]], Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--once with --no-account emits JSON from local data and exits 20."""
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    sessions_dir = make_sessions(
        [("c1", "/p", "haiku", 0.02, int(now.timestamp() * _MS))]
    )
    cfg = AppConfig(use_account=False)
    with httpx.Client() as client:
        ctx = RunContext(
            db_path=sessions_dir.parent,
            sessions_dir=sessions_dir,
            client=client,
            tz=UTC,
            holidays=None,
        )
        code = run_once(cfg, ctx, now=now, as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema_version"] == 1
    assert data["account_status"] == "disabled"
    assert data["account"] is None
    assert data["pace"] is None
    assert data["today"] == {"credits": 0.02, "turns": 1}
    assert data["usage"]["scope"] == "recent"
    assert data["usage"]["by_folder_model"] == [["/p", "haiku", 1, 0.02]]
    assert code == _EXIT_INDETERMINATE
