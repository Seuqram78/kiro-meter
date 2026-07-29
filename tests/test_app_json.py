"""Tests for the --json snapshot shape (kiro_meter.app._as_dict)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

from kiro_meter.app import _as_dict
from kiro_meter.models import Snapshot
from tests.conftest import account_info, db_snapshot, pace_info

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
_SCHEMA_VERSION = 1
_EXPECTED_TOTAL_CREDITS = 0.31
_EXPECTED_TOTAL_TURNS = 20


def _full_snapshot() -> Snapshot:
    return Snapshot(db_snapshot(), account_info(), "ok", pace_info(), _NOW)


def test_full_snapshot_has_every_nested_object() -> None:
    """A complete snapshot maps every group into its documented shape."""
    data = _as_dict(_full_snapshot())
    assert data["schema_version"] == _SCHEMA_VERSION
    assert data["account_status"] == "ok"
    assert data["account"]["tier"] == "KIRO FREE"
    assert data["account"]["used"] == account_info().used
    assert data["pace"]["mode"] == "calendar"
    assert data["today"] == {"credits": 0.31, "turns": 18}
    assert data["usage"]["scope"] == "this cycle"
    assert data["usage"]["by_folder_model"] == [
        ["/home/me/proj-a", "sonnet-4.5", 8, 0.21],
        ["/home/me/proj-b", "haiku-4.5", 12, 0.10],
    ]
    assert data["usage"]["total_credits"] == _EXPECTED_TOTAL_CREDITS
    assert data["usage"]["total_turns"] == _EXPECTED_TOTAL_TURNS


def test_needs_login_nulls_account_and_pace() -> None:
    """A needs_login snapshot has no account or pace data."""
    snap = Snapshot(db_snapshot(), None, "needs_login", None, _NOW)
    data = _as_dict(snap)
    assert data["account"] is None
    assert data["pace"] is None


def test_error_status_nulls_account_and_pace() -> None:
    """An error snapshot has the same null shape as needs_login, status aside."""
    snap = Snapshot(db_snapshot(), None, "error", None, _NOW)
    data = _as_dict(snap)
    assert data["account_status"] == "error"
    assert data["account"] is None
    assert data["pace"] is None


def test_empty_folder_model_nulls_usage() -> None:
    """No local usage data means the usage object is null, not an empty list."""
    db = replace(db_snapshot(), by_folder_model=())
    snap = Snapshot(db, account_info(), "ok", pace_info(), _NOW)
    data = _as_dict(snap)
    assert data["usage"] is None


def test_burn_rate_none_is_null_not_omitted() -> None:
    """A missing burn rate is represented as null, keeping the key present."""
    db = replace(db_snapshot(), burn_rate_per_min=None)
    snap = Snapshot(db, account_info(), "ok", pace_info(), _NOW)
    data = _as_dict(snap)
    assert "burn_rate_per_min" in data
    assert data["burn_rate_per_min"] is None


def test_projection_runout_none_inside_populated_pace() -> None:
    """A null projection_runout doesn't null out the rest of the pace object."""
    data = _as_dict(_full_snapshot())
    assert data["pace"]["projection_runout"] is None
    assert data["pace"]["allowance_per_day"] is not None


def test_full_snapshot_is_json_serialisable() -> None:
    """The dict never leaks a non-primitive (e.g. an un-isoformatted datetime)."""
    json.dumps(_as_dict(_full_snapshot()))
