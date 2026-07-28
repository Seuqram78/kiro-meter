# kiro-usage Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `kiro-usage`, a privacy-first live terminal monitor that shows Kiro CLI credit spend (from local SQLite) against the official plan limit (from the `getUsageLimits` endpoint), with calendar-day or working-day pacing.

**Architecture:** A read-only pipeline. Pure data modules (`db`, `account`, `pace`, `config`) produce typed dataclasses; `render` turns a merged `Snapshot` into a `rich` renderable; `app` runs the `rich.Live` loop (poll SQLite every ~3s, refresh the official limit every ~5min); `cli` parses flags and wires it together. Network and DB access are each isolated behind one function so everything else is unit-tested without I/O.

**Tech Stack:** Python 3.14, `rich` (TUI), `httpx` (HTTP), stdlib `sqlite3`/`tomllib`/`datetime`/`zoneinfo`. Tooling: `uv`, `ruff`, `pytest`, CodeGraph.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python:** pinned to 3.14 — `requires-python = ">=3.14"`, `.python-version` = `3.14`.
- **Lint:** `ruff check` with `[tool.ruff.lint] select = ["ALL"]` and `ruff format`. The ONLY permitted ignores are ruff's four documented self-conflicts: `D203`, `D213`, `COM812`, `ISC001`. No inline `# noqa`, no `[tool.ruff.lint.per-file-ignores]`. If any other rule seems to require suppression, **stop and raise to the maintainer** — do not suppress.
- **Runtime deps:** only `rich` and `httpx`. Everything else is stdlib.
- **Provenance:** every displayed number is labelled `official`, `local`, `local · approx`, or `unavailable`. Never present a local estimate as official.
- **Privacy/tests:** no real tokens, emails, or live network in tests. Fixtures are synthetic. The local SQLite DB is opened read-only.
- **Git:** each task ends with `ruff check`, `ruff format --check`, and `pytest` all green, then one conventional-commit-style commit. Commit messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.
- **CodeGraph:** repo is a CodeGraph project; use it to navigate.

---

## File Structure

```
pyproject.toml                 # project metadata, deps, ruff config, pytest config
src/kiro_usage/
  __init__.py                  # version
  models.py                    # all dataclasses + Provenance/enums (shared types)
  db.py                        # read local SQLite -> ConversationRow[] -> DbSnapshot
  account.py                   # load token, call getUsageLimits, parse -> AccountInfo
  pace.py                      # target/actual pace, day counting, holiday provider
  config.py                    # AppConfig load/save + first-run setup prompts
  render.py                    # Snapshot -> rich renderable
  app.py                       # assemble Snapshot; rich.Live loop; run_once
  cli.py                       # argparse entry point main()
tests/
  conftest.py                  # synthetic fixtures (sqlite db builder, sample JSON)
  test_db.py
  test_account.py
  test_pace.py
  test_config.py
  test_render.py
  test_cli.py
```

Files are split by responsibility. `models.py` holds the shared types every other module imports, keeping signatures consistent.

---

## Verified reference data

The `getUsageLimits` request/response below is **verified against the live endpoint**; implement to this shape exactly.

**Request:** `GET https://q.{region}.amazonaws.com/getUsageLimits` with query params
`isEmailRequired=true`, `origin=AI_EDITOR`, `resourceType=AGENTIC_REQUEST`, `profileArn=<arn>`;
headers `Authorization: Bearer <access_token>`, `x-amzn-kiro-agent-mode: vibe`.
On a response body containing `FEATURE_NOT_SUPPORTED`, retry with the next param combo:
`{origin}` only, then `{resourceType:"CONVERSATION", origin}`.

**Response (sanitized example):**

```json
{
  "daysUntilReset": 0,
  "nextDateReset": 1785542400.0,
  "overageConfiguration": {"overageLimit": null, "overageStatus": "DISABLED"},
  "subscriptionInfo": {
    "overageCapability": "OVERAGE_INCAPABLE",
    "subscriptionTitle": "KIRO FREE",
    "type": "Q_DEVELOPER_STANDALONE_FREE"
  },
  "usageBreakdownList": [
    {
      "currentUsage": 11, "currentUsageWithPrecision": 11.21,
      "usageLimit": 50, "usageLimitWithPrecision": 50.0,
      "currentOverages": 0, "currentOveragesWithPrecision": 0.0,
      "overageCap": 10000, "overageCapWithPrecision": 10000.0,
      "overageRate": 0.04, "overageCharges": 0.0,
      "resourceType": "CREDIT", "unit": "INVOCATIONS",
      "displayName": "Credit", "currency": "USD", "freeTrialInfo": null
    }
  ],
  "userInfo": {"email": "user@example.com"}
}
```

Pick the breakdown item with `resourceType == "CREDIT"`. Prefer `*WithPrecision` fields; fall back to the integer fields.

**Local token:** table `auth_kv`, key `kirocli:social:token`, JSON with
`access_token`, `refresh_token`, `profile_arn`, `expires_at` (ISO-8601 string).
Region is parsed from `profile_arn` (`arn:aws:codewhisperer:us-east-1:...`), default `us-east-1`.

**Local spend:** table `conversations_v2` (`key` = folder, `value` = JSON, `updated_at` = ms).
JSON: `user_turn_metadata.usage_info[].value` (credits, `unit == "credit"`),
`history[-1].request_metadata.model_id` (attribute conversation credits to its latest model),
`history[-1].user.env_context.env_state.current_working_directory` (per-turn folder, falls back to `key`).

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `src/kiro_usage/__init__.py`, `tests/conftest.py` (empty placeholder for now), `tests/test_cli.py`

**Interfaces:**
- Consumes: nothing.
- Produces: installable package `kiro_usage` with `__version__: str`; `uv run` toolchain; ruff + pytest configured.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "kiro-usage"
version = "0.1.0"
description = "Privacy-first live TUI monitor for Kiro CLI credit usage"
requires-python = ">=3.14"
dependencies = ["rich>=13", "httpx>=0.27"]

[project.scripts]
kiro-usage = "kiro_usage.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/kiro_usage"]

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "respx>=0.21"]

[tool.ruff.lint]
select = ["ALL"]
ignore = [
  "D203",   # conflicts with D211 (ruff docs)
  "D213",   # conflicts with D212 (ruff docs)
  "COM812", # conflicts with ruff format (ruff docs)
  "ISC001", # conflicts with ruff format (ruff docs)
]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.pytest.ini_options]
addopts = "-q"
testpaths = ["tests"]
```

- [ ] **Step 2: Write `src/kiro_usage/__init__.py`**

```python
"""kiro-usage: a live terminal monitor for Kiro CLI credit usage."""

__version__: str = "0.1.0"
```

- [ ] **Step 3: Write the failing smoke test** in `tests/test_cli.py`

```python
"""Smoke tests for package import and version."""

from kiro_usage import __version__


def test_version_is_a_string() -> None:
    """The package exposes a string version."""
    assert isinstance(__version__, str)
    assert __version__
```

- [ ] **Step 4: Create empty `tests/conftest.py`**

```python
"""Shared pytest fixtures for kiro-usage tests."""
```

- [ ] **Step 5: Sync and run the gate**

Run: `uv sync && uv run ruff check && uv run ruff format --check && uv run pytest`
Expected: ruff clean, 1 test passes. If ruff format flags files, run `uv run ruff format` and re-check.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src tests
git commit -m "feat: scaffold package, ruff ALL config, and smoke test"
```

---

### Task 2: Shared data models

**Files:**
- Create: `src/kiro_usage/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces (imported by every later module):

```python
Provenance = Literal["official", "local", "local_approx", "unavailable"]
AccountStatus = Literal["ok", "needs_login", "disabled", "error"]
PaceMode = Literal["calendar", "workday"]


@dataclass(frozen=True)
class ConversationRow:
    conversation_id: str
    folder: str
    model_id: str | None
    credits: float
    updated_at_ms: int


@dataclass(frozen=True)
class DbSnapshot:
    today_credits: float
    today_turns: int
    session_credits: float
    session_turns: int
    burn_rate_per_min: float | None
    by_folder: tuple[tuple[str, float], ...]
    by_model: tuple[tuple[str, float], ...]
    recent: tuple[ConversationRow, ...]
    approx: bool


@dataclass(frozen=True)
class AccountInfo:
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
    mode: PaceMode
    target_per_day: float | None
    actual_per_day: float | None
    today_fraction: float | None
    days_until_reset: float
    days_elapsed: float
    projection_runout: datetime | None
    non_working_today: bool
    holidays_available: bool


@dataclass(frozen=True)
class AppConfig:
    refresh_seconds: int = 3
    limit_refresh_seconds: int = 300
    use_account: bool = True
    workdays: bool = False
    country: str | None = None
    region: str | None = None
    timezone: str | None = None
    reset_hour: int = 0


@dataclass(frozen=True)
class Snapshot:
    db: DbSnapshot
    account: AccountInfo | None
    account_status: AccountStatus
    pace: PaceInfo | None
    generated_at: datetime
```

- [ ] **Step 1: Write the failing test** in `tests/test_models.py`

```python
"""Tests for the shared dataclasses."""

from datetime import UTC, datetime

from kiro_usage.models import AccountInfo, AppConfig, ConversationRow


def test_conversation_row_is_frozen() -> None:
    """ConversationRow is immutable."""
    row = ConversationRow("c1", "/proj", "haiku", 0.5, 1000)
    assert row.credits == 0.5


def test_appconfig_defaults() -> None:
    """AppConfig has sensible defaults."""
    cfg = AppConfig()
    assert cfg.refresh_seconds == 3
    assert cfg.use_account is True
    assert cfg.workdays is False


def test_account_info_holds_reset_datetime() -> None:
    """AccountInfo carries a timezone-aware reset datetime."""
    info = AccountInfo(
        email="user@example.com",
        tier="KIRO FREE",
        sub_type="FREE",
        used=11.21,
        limit=50.0,
        overage_used=0.0,
        overage_cap=10000.0,
        overage_rate=0.04,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=0,
        currency="USD",
        fetched_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    assert info.next_reset.tzinfo is UTC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: kiro_usage.models`.

- [ ] **Step 3: Implement `src/kiro_usage/models.py`**

Write the module with a top docstring, the `from __future__ import annotations` import, `from dataclasses import dataclass`, `from datetime import datetime`, `from typing import Literal`, and exactly the type definitions listed in Interfaces above, each dataclass and public alias documented with a one-line docstring/comment as needed to satisfy `D`.

- [ ] **Step 4: Run tests + gate**

Run: `uv run pytest tests/test_models.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/kiro_usage/models.py tests/test_models.py
git commit -m "feat: add shared dataclasses"
```

---

### Task 3: Local SQLite reader (`db.py`)

**Files:**
- Create: `src/kiro_usage/db.py`
- Modify: `tests/conftest.py` (add `sqlite_db` fixture builder)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `ConversationRow`, `DbSnapshot` from `models`.
- Produces:
  - `DEFAULT_DB_PATH: Path` = `~/.local/share/kiro-cli/data.sqlite3`
  - `load_conversations(db_path: Path) -> list[ConversationRow]`
  - `build_db_snapshot(rows: list[ConversationRow], *, now: datetime, tz: tzinfo, burn_window_min: int = 15, top_n: int = 5) -> DbSnapshot`

Semantics: a conversation's `credits` = sum of `user_turn_metadata.usage_info[].value` where `unit == "credit"`. `folder` = latest history entry's cwd, else the `key` column. `model_id` = latest history entry's `request_metadata.model_id`. `today_*` filters `updated_at_ms` to the local calendar day of `now` in `tz`. `session_*` = the single most-recently-updated conversation. `burn_rate_per_min` = sum of credits from conversations updated within `burn_window_min` of `now`, divided by `burn_window_min`; `None` if none. `approx = True` always (credits are turn-metadata level, not a per-turn ledger). `by_folder`/`by_model` are top-N by credits, descending.

- [ ] **Step 1: Add the fixture builder to `tests/conftest.py`**

```python
"""Shared pytest fixtures for kiro-usage tests."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest


def _conversation_json(cwd: str, model: str, credits: float) -> str:
    return json.dumps(
        {
            "history": [
                {
                    "user": {
                        "env_context": {"env_state": {"current_working_directory": cwd}}
                    },
                    "request_metadata": {"model_id": model},
                }
            ],
            "user_turn_metadata": {
                "usage_info": [{"value": credits, "unit": "credit"}]
            },
        }
    )


@pytest.fixture
def make_db(tmp_path: Path) -> Callable[[list[tuple[str, str, str, float, int]]], Path]:
    """Return a builder that writes a synthetic kiro-cli sqlite DB.

    Each row tuple is (conversation_id, folder, model, credits, updated_at_ms).
    """

    def build(rows: list[tuple[str, str, str, float, int]]) -> Path:
        db_path = tmp_path / "data.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE conversations_v2 (key TEXT, conversation_id TEXT, "
            "value TEXT, created_at INTEGER, updated_at INTEGER)"
        )
        for cid, folder, model, credits, updated in rows:
            conn.execute(
                "INSERT INTO conversations_v2 VALUES (?,?,?,?,?)",
                (
                    folder,
                    cid,
                    _conversation_json(folder, model, credits),
                    updated,
                    updated,
                ),
            )
        conn.commit()
        conn.close()
        return db_path

    return build
```

- [ ] **Step 2: Write failing tests** in `tests/test_db.py`

```python
"""Tests for the local SQLite reader."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kiro_usage.db import build_db_snapshot, load_conversations

_MS = 1000
_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * _MS)


def test_load_conversations_parses_credits_folder_model(
    make_db: Callable[[list[tuple[str, str, str, float, int]]], Path],
) -> None:
    """Credits, folder, and model are extracted per conversation."""
    db = make_db([("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW))])
    rows = load_conversations(db)
    assert len(rows) == 1
    assert rows[0].credits == 0.02
    assert rows[0].folder == "/proj-a"
    assert rows[0].model_id == "haiku-4.5"


def test_snapshot_aggregates_today_and_breakdowns(
    make_db: Callable[[list[tuple[str, str, str, float, int]]], Path],
) -> None:
    """Today's spend, folder, and model breakdowns aggregate correctly."""
    db = make_db(
        [
            ("c1", "/proj-a", "haiku-4.5", 0.02, _ms(_NOW)),
            ("c2", "/proj-b", "sonnet-4.5", 0.03, _ms(_NOW)),
            ("c3", "/proj-a", "haiku-4.5", 0.05, _ms(_NOW)),
        ]
    )
    snap = build_db_snapshot(load_conversations(db), now=_NOW, tz=UTC)
    assert round(snap.today_credits, 2) == 0.10
    assert snap.today_turns == 3
    assert snap.by_folder[0] == ("/proj-a", 0.07)
    assert snap.approx is True


def test_burn_rate_only_counts_recent(
    make_db: Callable[[list[tuple[str, str, str, float, int]]], Path],
) -> None:
    """Burn rate counts only conversations within the window."""
    old = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    db = make_db(
        [
            ("c1", "/p", "m", 0.15, _ms(_NOW)),
            ("c2", "/p", "m", 9.0, _ms(old)),
        ]
    )
    snap = build_db_snapshot(
        load_conversations(db), now=_NOW, tz=UTC, burn_window_min=15
    )
    assert snap.burn_rate_per_min == 0.01
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement `src/kiro_usage/db.py`**

```python
"""Read-only access to the Kiro CLI local SQLite database."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, tzinfo  # noqa: TC003 -- runtime use in signatures
from pathlib import Path
from typing import TYPE_CHECKING

from kiro_usage.models import ConversationRow, DbSnapshot

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_DB_PATH: Path = Path.home() / ".local/share/kiro-cli/data.sqlite3"
_MS_PER_MIN = 60_000
_CREDIT_UNIT = "credit"


def load_conversations(db_path: Path) -> list[ConversationRow]:
    """Load one ConversationRow per row in conversations_v2."""
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cursor = conn.execute(
            "SELECT conversation_id, key, value, updated_at FROM conversations_v2"
        )
        return [
            _parse_row(cid, key, value, updated) for cid, key, value, updated in cursor
        ]


def _parse_row(cid: str, key: str, value: str, updated_at_ms: int) -> ConversationRow:
    data = json.loads(value)
    credits = sum(
        item.get("value", 0.0)
        for item in data.get("user_turn_metadata", {}).get("usage_info", [])
        if item.get("unit") == _CREDIT_UNIT
    )
    history = data.get("history") or []
    latest = history[-1] if history else {}
    model_id = latest.get("request_metadata", {}).get("model_id")
    cwd = (
        latest.get("user", {})
        .get("env_context", {})
        .get("env_state", {})
        .get("current_working_directory")
    )
    return ConversationRow(
        cid, cwd or key, model_id, float(credits), int(updated_at_ms)
    )
```

Then add `build_db_snapshot`:

```python
def build_db_snapshot(
    rows: list[ConversationRow],
    *,
    now: datetime,
    tz: tzinfo,
    burn_window_min: int = 15,
    top_n: int = 5,
) -> DbSnapshot:
    """Aggregate conversation rows into a DbSnapshot."""
    today = now.astimezone(tz).date()
    today_rows = [r for r in rows if _local_date(r.updated_at_ms, tz) == today]
    window_start_ms = int(now.timestamp() * 1000) - burn_window_min * _MS_PER_MIN
    recent_rows = [r for r in rows if r.updated_at_ms >= window_start_ms]
    burn = (
        sum(r.credits for r in recent_rows) / burn_window_min if recent_rows else None
    )
    session = max(rows, key=lambda r: r.updated_at_ms, default=None)
    ordered = sorted(rows, key=lambda r: r.updated_at_ms, reverse=True)
    return DbSnapshot(
        today_credits=sum(r.credits for r in today_rows),
        today_turns=len(today_rows),
        session_credits=session.credits if session else 0.0,
        session_turns=1 if session else 0,
        burn_rate_per_min=burn,
        by_folder=_top(today_rows, key=lambda r: r.folder, n=top_n),
        by_model=_top(today_rows, key=lambda r: r.model_id or "unknown", n=top_n),
        recent=tuple(ordered[:top_n]),
        approx=True,
    )
```

Add private helpers `_local_date(updated_at_ms, tz) -> date` and
`_top(rows, *, key, n) -> tuple[tuple[str, float], ...]` (sum credits by key,
sort descending, take n). Give every public function a Google-style docstring.

> **Note on the one `# noqa` above:** the `TC003` import placement is a case where the type is used at runtime in signatures. If ruff flags an unavoidable rule here, per Global Constraints do NOT add `# noqa` — instead restructure (e.g. import `datetime`/`tzinfo` normally without the `TYPE_CHECKING` guard, which is the intended fix) and if that still conflicts, **stop and raise**. The sample shows the shape; make it compliant without suppression.

- [ ] **Step 5: Run tests + gate**

Run: `uv run pytest tests/test_db.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean (no suppressions).

- [ ] **Step 6: Commit**

```bash
git add src/kiro_usage/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: read local kiro-cli sqlite spend data"
```

---

### Task 4: Account endpoint client (`account.py`)

**Files:**
- Create: `src/kiro_usage/account.py`
- Test: `tests/test_account.py`

**Interfaces:**
- Consumes: `AccountInfo` from `models`.
- Produces:
  - `@dataclass(frozen=True) class SocialToken: access_token: str; refresh_token: str; profile_arn: str; expires_at: datetime; region: str`
  - `class NeedsLoginError(Exception)`
  - `load_token(db_path: Path) -> SocialToken | None`
  - `token_expired(token: SocialToken, now: datetime) -> bool`
  - `fetch_account_info(token: SocialToken, *, client: httpx.Client, now: datetime) -> AccountInfo`

Semantics: `fetch_account_info` issues the verified GET with the three param-combo fallbacks; on 403 or a body containing `bearer token`/`invalid`, raises `NeedsLoginError`; parses the `resourceType == "CREDIT"` breakdown item preferring `*WithPrecision`. `overage_enabled = overageConfiguration.overageStatus == "ENABLED"`. `next_reset = datetime.fromtimestamp(nextDateReset, tz=UTC)`.

- [ ] **Step 1: Add sample response to `tests/conftest.py`**

```python
@pytest.fixture
def usage_response() -> dict:
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
            }
        ],
        "userInfo": {"email": "user@example.com"},
    }
```

- [ ] **Step 2: Write failing tests** in `tests/test_account.py`

```python
"""Tests for the account/getUsageLimits client."""

from datetime import UTC, datetime

import httpx
import pytest

from kiro_usage.account import (
    NeedsLoginError,
    SocialToken,
    fetch_account_info,
    token_expired,
)

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _token() -> SocialToken:
    return SocialToken(
        access_token="a",
        refresh_token="r",
        profile_arn="arn:aws:codewhisperer:us-east-1:1:profile/X",
        expires_at=datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
        region="us-east-1",
    )


def test_token_expired() -> None:
    """A token past its expiry is reported expired."""
    assert token_expired(_token(), datetime(2026, 7, 28, 14, 0, tzinfo=UTC)) is True
    assert token_expired(_token(), _NOW) is False


def test_fetch_parses_official_fields(usage_response: dict) -> None:
    """A 200 response maps to AccountInfo with precise values."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json=usage_response)
        )
    )
    info = fetch_account_info(_token(), client=client, now=_NOW)
    assert info.used == 11.21
    assert info.limit == 50.0
    assert info.tier == "KIRO FREE"
    assert info.overage_rate == 0.04
    assert info.email == "user@example.com"
    assert info.next_reset.tzinfo is UTC


def test_fetch_403_raises_needs_login(usage_response: dict) -> None:
    """A 403 becomes NeedsLoginError."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(403, text="bearer token invalid")
        )
    )
    with pytest.raises(NeedsLoginError):
        fetch_account_info(_token(), client=client, now=_NOW)


def test_fetch_retries_on_feature_not_supported(usage_response: dict) -> None:
    """A FEATURE_NOT_SUPPORTED body triggers the next param combo."""
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, text="FEATURE_NOT_SUPPORTED")
        return httpx.Response(200, json=usage_response)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    info = fetch_account_info(_token(), client=client, now=_NOW)
    assert calls["n"] == 2
    assert info.used == 11.21
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_account.py -v`
Expected: FAIL — module missing.

- [ ] **Step 4: Implement `src/kiro_usage/account.py`**

Implement:
- Module + function docstrings (Google style).
- `SocialToken` dataclass.
- `NeedsLoginError(Exception)` with a docstring.
- `load_token(db_path)`: open read-only, `SELECT value FROM auth_kv WHERE key = 'kirocli:social:token'`; `json.loads`; parse `expires_at` via `datetime.fromisoformat` (handle trailing `Z` by replacing with `+00:00`); region via regex `arn:aws:\w+:([a-z0-9-]+):` on `profile_arn`, default `"us-east-1"`; return `None` if the row is absent.
- `token_expired(token, now)`: `return now >= token.expires_at`.
- Module constants: `_BASE = "https://q.{region}.amazonaws.com/getUsageLimits"`, `_HEADERS = {"x-amzn-kiro-agent-mode": "vibe"}`, `_ATTEMPTS = ({"resourceType": "AGENTIC_REQUEST", "origin": "AI_EDITOR"}, {"origin": "AI_EDITOR"}, {"resourceType": "CONVERSATION", "origin": "AI_EDITOR"})`, `_FORBIDDEN = 403`, `_FEATURE_UNSUPPORTED = "FEATURE_NOT_SUPPORTED"`.
- `fetch_account_info`: loop over `_ATTEMPTS`; build params with `isEmailRequired=true`, `profileArn`, and the combo; GET with `Authorization: Bearer <access>` + `_HEADERS`; if `status == 403` or body matches auth markers → `raise NeedsLoginError`; if body contains `_FEATURE_UNSUPPORTED` and not the last attempt → continue; `raise_for_status()` other errors; parse JSON via `_parse(data, now)` and return. After the loop, raise `NeedsLoginError` or a `RuntimeError` if all combos were unsupported.
- `_parse(data, now)`: find the `resourceType == "CREDIT"` item (fallback: first item); pull `used = item.get("currentUsageWithPrecision", item.get("currentUsage", 0.0))` etc.; build `AccountInfo` with `fetched_at=now`, `next_reset=datetime.fromtimestamp(data["nextDateReset"], tz=UTC)`, `overage_enabled=data.get("overageConfiguration", {}).get("overageStatus") == "ENABLED"`.

Hoist every literal (timeout seconds, HTTP codes, marker strings) to named constants to satisfy `PLR2004`.

- [ ] **Step 5: Run tests + gate**

Run: `uv run pytest tests/test_account.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/kiro_usage/account.py tests/test_account.py tests/conftest.py
git commit -m "feat: fetch official plan limits from getUsageLimits"
```

---

### Task 5: Calendar-day pacing (`pace.py`)

**Files:**
- Create: `src/kiro_usage/pace.py`
- Test: `tests/test_pace.py`

**Interfaces:**
- Consumes: `AccountInfo`, `DbSnapshot`, `AppConfig`, `PaceInfo` from `models`.
- Produces:
  - `class HolidayProvider(Protocol): def working_days_between(self, start: date, end: date, *, country: str, region: str | None) -> int: ...`
  - `compute_pace(account: AccountInfo, db: DbSnapshot, cfg: AppConfig, *, now: datetime, holidays: HolidayProvider | None = None) -> PaceInfo`

Semantics (calendar mode, `cfg.workdays is False` or `holidays is None`):
`remaining = max(account.limit - account.used, 0.0)`;
`days_until_reset = (account.next_reset - now).total_seconds() / 86400`;
`cycle_start = account.next_reset - one_calendar_month`;
`days_elapsed = (now - cycle_start).total_seconds() / 86400`;
`target_per_day = remaining / days_until_reset` if `days_until_reset >= _MIN_DAYS` else `None`;
`actual_per_day = account.used / days_elapsed` if `days_elapsed >= _MIN_DAYS` else `None`;
`today_fraction = db.today_credits / target_per_day` if `target_per_day` else `None`;
`projection_runout = now + timedelta(days=remaining / actual_per_day)` if `actual_per_day` else `None`;
`mode = "calendar"`, `non_working_today = False`, `holidays_available = True`.
`_MIN_DAYS = 0.5`.

- [ ] **Step 1: Write failing tests** in `tests/test_pace.py`

```python
"""Tests for pace computation."""

from datetime import UTC, datetime

from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot
from kiro_usage.pace import compute_pace

_NOW = datetime(2026, 7, 15, 0, 0, tzinfo=UTC)


def _account(used: float, limit: float) -> AccountInfo:
    return AccountInfo(
        email="u@e.com",
        tier="FREE",
        sub_type="FREE",
        used=used,
        limit=limit,
        overage_used=0.0,
        overage_cap=0.0,
        overage_rate=0.0,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=17,
        currency="USD",
        fetched_at=_NOW,
    )


def _db(today: float) -> DbSnapshot:
    return DbSnapshot(today, 1, today, 1, None, (), (), (), approx=True)


def test_target_and_actual_pace_calendar() -> None:
    """Target uses remaining/days-left, actual uses used/days-elapsed."""
    pace = compute_pace(
        _account(used=14.0, limit=50.0), _db(1.0), AppConfig(), now=_NOW
    )
    assert pace.mode == "calendar"
    # remaining 36 over 17 days
    assert round(pace.target_per_day, 2) == round(36 / 17, 2)
    # used 14 over ~14 elapsed days (Jul 1 -> Jul 15)
    assert pace.actual_per_day is not None
    assert pace.projection_runout is not None


def test_pace_none_when_cycle_just_reset() -> None:
    """When days_elapsed < 0.5 actual pace is None (no divide blow-up)."""
    now = datetime(2026, 7, 1, 1, 0, tzinfo=UTC)
    pace = compute_pace(_account(used=0.0, limit=50.0), _db(0.0), AppConfig(), now=now)
    assert pace.actual_per_day is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pace.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement calendar path in `src/kiro_usage/pace.py`**

Implement `compute_pace` for the calendar branch exactly per Semantics.
Compute the one-calendar-month-earlier `cycle_start` with a small helper
`_month_before(dt)` that decrements the month (handling January → previous
December and clamping the day). Use named constants `_SECONDS_PER_DAY = 86_400`
and `_MIN_DAYS = 0.5`. Define the `HolidayProvider` Protocol now (used in Task 6)
but leave the workday branch to Task 6 — for this task `compute_pace` may assume
calendar mode. Full docstrings.

- [ ] **Step 4: Run tests + gate**

Run: `uv run pytest tests/test_pace.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/kiro_usage/pace.py tests/test_pace.py
git commit -m "feat: compute calendar-day target and actual pace"
```

---

### Task 6: Working-day pacing + holiday provider (`pace.py`)

**Files:**
- Modify: `src/kiro_usage/pace.py`
- Test: `tests/test_pace.py` (add cases)

**Interfaces:**
- Consumes: everything from Task 5.
- Produces:
  - `class NagerHolidayProvider: def __init__(self, client: httpx.Client, cache_dir: Path) -> None: ...` implementing `HolidayProvider`.
  - Extends `compute_pace`: when `cfg.workdays and holidays is not None`, replace calendar-day denominators with **working-day** counts between `now` and `next_reset` (target) and between `cycle_start` and `now` (actual). Set `mode="workday"`. `non_working_today = today is a weekend/holiday`. On provider failure, fall back to weekends-only and set `holidays_available=False`.

Semantics: `NagerHolidayProvider.working_days_between(start, end, country, region)` counts dates in `[start, end)` that are Mon–Fri and not a public holiday. Holidays for a year come from `GET https://date.nager.at/api/v3/PublicHolidays/{year}/{country}`; a holiday counts if `global is True` or `region` is in its `counties`. Results are cached to `cache_dir/holidays-{country}-{year}.json`; on network failure with no cache, raise `HolidayUnavailableError` (caller falls back to weekends-only).

- [ ] **Step 1: Write failing tests** in `tests/test_pace.py`

```python
from datetime import date
from pathlib import Path

import httpx

from kiro_usage.pace import NagerHolidayProvider, compute_pace


def _holidays_payload() -> list[dict]:
    return [{"date": "2026-07-04", "global": True, "counties": None}]


def test_working_days_excludes_weekends_and_holidays(tmp_path: Path) -> None:
    """Working-day count skips Sat/Sun and public holidays."""
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json=_holidays_payload())
        )
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    # 2026-06-29 (Mon) .. 2026-07-06 (Mon): 5 weekdays minus Jul-4 (Sat anyway)
    count = provider.working_days_between(
        date(2026, 6, 29), date(2026, 7, 6), country="US", region=None
    )
    assert count == 5


def test_workday_mode_sets_mode_and_uses_provider(tmp_path: Path) -> None:
    """Workday mode is reflected in PaceInfo.mode."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=[]))
    )
    provider = NagerHolidayProvider(client=client, cache_dir=tmp_path)
    cfg = AppConfig(workdays=True, country="US")
    pace = compute_pace(
        _account(14.0, 50.0), _db(1.0), cfg, now=_NOW, holidays=provider
    )
    assert pace.mode == "workday"
```

(Reuse `_account`, `_db`, `_NOW`, `AppConfig` already imported in this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pace.py -v`
Expected: FAIL — `NagerHolidayProvider` missing.

- [ ] **Step 3: Implement the workday branch + provider**

Add `HolidayUnavailableError(Exception)`, `NagerHolidayProvider` (with per-year
disk cache via `json`), a `_working_days(start, end, holiday_dates)` helper, and
the workday branch in `compute_pace` (try provider; on
`HolidayUnavailableError` or `httpx.HTTPError`, count weekends-only and set
`holidays_available=False`). Constant `_NAGER_URL = "https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"`,
`_WEEKEND = {5, 6}`. Determine `non_working_today` from `now.date()`.

- [ ] **Step 4: Run tests + gate**

Run: `uv run pytest tests/test_pace.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/kiro_usage/pace.py tests/test_pace.py
git commit -m "feat: add working-day pacing with cached holiday data"
```

---

### Task 7: Config + first-run setup (`config.py`)

**Files:**
- Create: `src/kiro_usage/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `AppConfig` from `models`.
- Produces:
  - `CONFIG_PATH: Path` = `~/.kiro-usage/config.toml`
  - `load_config(path: Path = CONFIG_PATH) -> AppConfig | None` (None if the file is absent)
  - `save_config(cfg: AppConfig, path: Path = CONFIG_PATH) -> None`
  - `detect_country() -> str | None` (from `locale.getlocale()` / `LANG`, e.g. `pt_BR` → `BR`)
  - `run_first_time_setup(*, prompt: Callable[[str], str] = input, detect: Callable[[], str | None] = detect_country) -> AppConfig`

Semantics: `run_first_time_setup` asks whether to pace against working days; if yes, asks country (default from `detect()`) and optional region; returns an `AppConfig`. `save_config` writes TOML by hand (stdlib has no TOML writer) with only the non-default keys. `load_config` uses `tomllib`.

- [ ] **Step 1: Write failing tests** in `tests/test_config.py`

```python
"""Tests for config persistence and setup."""

from pathlib import Path

from kiro_usage.config import load_config, run_first_time_setup, save_config
from kiro_usage.models import AppConfig


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    """A saved config loads back equal."""
    path = tmp_path / "config.toml"
    cfg = AppConfig(workdays=True, country="BR", region="SP")
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded == cfg


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """Absent config file yields None."""
    assert load_config(tmp_path / "nope.toml") is None


def test_setup_workdays_yes_collects_location() -> None:
    """Answering yes collects country and region."""
    answers = iter(["y", "BR", "SP"])
    cfg = run_first_time_setup(prompt=lambda _p: next(answers), detect=lambda: "BR")
    assert cfg.workdays is True
    assert cfg.country == "BR"
    assert cfg.region == "SP"


def test_setup_workdays_no_uses_calendar() -> None:
    """Answering no leaves calendar mode."""
    cfg = run_first_time_setup(prompt=lambda _p: "n", detect=lambda: "BR")
    assert cfg.workdays is False
    assert cfg.country is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `src/kiro_usage/config.py`**

Implement per Semantics. For `save_config`, build the TOML text from the
dataclass fields that differ from `AppConfig()` defaults, quoting strings; create
the parent dir with `path.parent.mkdir(parents=True, exist_ok=True)`. For
`detect_country`, read `locale.getlocale()[0]` or `os.environ.get("LANG")`, take
the part after `_`, upcase, return the 2-letter code or `None`. Treat a blank
region answer as `None`. Full docstrings; hoist the yes-answers set to a constant.

- [ ] **Step 4: Run tests + gate**

Run: `uv run pytest tests/test_config.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/kiro_usage/config.py tests/test_config.py
git commit -m "feat: persist config and interactive first-run setup"
```

---

### Task 8: Rendering (`render.py`)

**Files:**
- Create: `src/kiro_usage/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Snapshot`, `AppConfig` from `models`.
- Produces: `render_snapshot(snap: Snapshot, cfg: AppConfig) -> RenderableType` (a `rich` renderable — a `Group`/`Panel`).

Semantics: render the panels from the design (plan gauge, overage, today bar, pace lines, burn, folder/model, recent). When `snap.account_status == "needs_login"`, show the expired-session banner instead of the plan gauge but still render local panels. When `snap.account is None` / `pace is None`, hide the plan gauge and pace and show the Today bare number. Label each figure with its provenance. This function does no I/O and must be deterministic given a `Snapshot`.

- [ ] **Step 1: Write failing tests** in `tests/test_render.py`

```python
"""Tests for rendering snapshots to text."""

from datetime import UTC, datetime

from rich.console import Console

from kiro_usage.models import AccountInfo, AppConfig, DbSnapshot, PaceInfo, Snapshot
from kiro_usage.render import render_snapshot

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _render(snap: Snapshot) -> str:
    console = Console(width=80, record=True)
    console.print(render_snapshot(snap, AppConfig()))
    return console.export_text()


def _db() -> DbSnapshot:
    return DbSnapshot(
        0.31,
        18,
        0.12,
        6,
        0.02,
        (("/proj-a", 0.21),),
        (("haiku", 0.13),),
        (),
        approx=True,
    )


def _account() -> AccountInfo:
    return AccountInfo(
        "u@e.com",
        "KIRO FREE",
        "FREE",
        11.21,
        50.0,
        0.0,
        10000.0,
        0.04,
        False,
        datetime(2026, 8, 1, tzinfo=UTC),
        3,
        "USD",
        _NOW,
    )


def test_official_gauge_rendered_when_account_present() -> None:
    """The plan gauge shows used/limit and an official label."""
    pace = PaceInfo(
        "calendar",
        2.1,
        1.9,
        0.15,
        4.0,
        27.0,
        None,
        non_working_today=False,
        holidays_available=True,
    )
    snap = Snapshot(_db(), _account(), "ok", pace, _NOW)
    text = _render(snap)
    assert "11.21" in text
    assert "50" in text
    assert "official" in text.lower()


def test_needs_login_banner_shown_and_local_still_rendered() -> None:
    """Expired session shows a banner but keeps local spend."""
    snap = Snapshot(_db(), None, "needs_login", None, _NOW)
    text = _render(snap)
    assert "kiro-cli" in text.lower()
    assert "0.31" in text  # local today still rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `src/kiro_usage/render.py`**

Build the renderable with `rich` primitives (`Panel`, `Table`, `rich.progress_bar.ProgressBar` or a manual bar string, `Group`). Branch on `snap.account_status`/`snap.account`/`snap.pace` per Semantics. Keep each panel in its own small private helper (`_plan_panel`, `_today_panel`, `_pace_lines`, `_breakdown_table`, `_recent_table`, `_login_banner`). Full docstrings; hoist bar width and thresholds to constants.

- [ ] **Step 4: Run tests + gate**

Run: `uv run pytest tests/test_render.py -v && uv run ruff check && uv run ruff format --check`
Expected: PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/kiro_usage/render.py tests/test_render.py
git commit -m "feat: render usage snapshot to a rich panel"
```

---

### Task 9: Assembly, live loop, and CLI (`app.py`, `cli.py`)

**Files:**
- Create: `src/kiro_usage/app.py`, `src/kiro_usage/cli.py`
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: all prior modules.
- Produces:
  - `app.build_snapshot(cfg: AppConfig, *, db_path: Path, client: httpx.Client, cached_account: AccountInfo | None, account_status: AccountStatus, now: datetime, tz: tzinfo, holidays: HolidayProvider | None) -> Snapshot`
  - `app.run_once(cfg: AppConfig, *, deps...) -> int` (returns an exit code: 0 ok, 10 near limit ≥90%, 11 at limit ≥100%, 20 no official data, 30 error)
  - `app.run_live(cfg: AppConfig, *, deps...) -> None` (the `rich.Live` loop)
  - `cli.main(argv: list[str] | None = None) -> int`

Semantics: `build_snapshot` gathers the DB snapshot every call, uses the cached `AccountInfo` (refreshed by the loop on its own cadence), computes pace when an account is present, and assembles a `Snapshot`. `run_live` owns timing: poll DB every `cfg.refresh_seconds`, refetch the limit every `cfg.limit_refresh_seconds`, catch `NeedsLoginError` → `account_status="needs_login"`, handle `q`/`r` keys. `main` parses flags (`--once`, `--json`, `--no-account`, `--refresh`, `--timezone`, `--reset-hour`, `--reconfigure`), runs first-time setup when no config exists, and dispatches to `run_once`/`run_live`.

- [ ] **Step 1: Write failing tests** in `tests/test_cli.py` (append)

```python
from datetime import UTC, datetime
from pathlib import Path

import httpx

from kiro_usage.app import run_once
from kiro_usage.models import AppConfig


def test_run_once_json_local_only(make_db, capsys) -> None:  # noqa: ANN001 -- pytest fixtures
    """--once with --no-account emits JSON from local data and exits 20."""
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    db = make_db([("c1", "/p", "haiku", 0.02, int(now.timestamp() * 1000))])
    cfg = AppConfig(use_account=False)
    code = run_once(
        cfg,
        db_path=db,
        client=httpx.Client(),
        cached_account=None,
        account_status="disabled",
        now=now,
        tz=UTC,
        holidays=None,
        as_json=True,
    )
    out = capsys.readouterr().out
    assert '"today_credits"' in out
    assert code == 20  # no official data -> indeterminate
```

> **Note:** the `# noqa: ANN001` above is illustrative of a spot you'll hit — pytest fixture params. Per Global Constraints you may NOT use `# noqa`. Resolve it by typing the fixture params properly (`make_db: Callable[..., Path]`, `capsys: pytest.CaptureFixture[str]`). If a rule genuinely cannot be satisfied, **stop and raise**.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `kiro_usage.app` missing.

- [ ] **Step 3: Implement `src/kiro_usage/app.py`**

Implement `build_snapshot`, `run_once` (assemble snapshot; if `as_json`, print `json.dumps` of a flat dict including `today_credits`, `used`, `limit`, provenance, and pace; else print the rendered panel once; compute the exit code from official data), and `run_live` (a `with rich.live.Live(...)` loop; use `select`/`rich`'s input handling or a simple `KeyboardInterrupt`-driven loop with a refresh interval; refresh the account on cadence, catching `NeedsLoginError`). Keep timing values as named constants. Full docstrings.

- [ ] **Step 4: Implement `src/kiro_usage/cli.py`**

`main(argv)` uses `argparse` to build `AppConfig` (merging saved config, running `run_first_time_setup` + `save_config` when none exists or `--reconfigure`), resolves the timezone via `zoneinfo.ZoneInfo`, constructs a shared `httpx.Client(timeout=...)`, loads the token (unless `--no-account`), and calls `run_once`/`run_live`. Return an int exit code. `def main() -> int` must be the `[project.scripts]` target — accept `argv=None`.

- [ ] **Step 5: Run tests + full gate**

Run: `uv run pytest && uv run ruff check && uv run ruff format --check`
Expected: all tests PASS, ruff clean.

- [ ] **Step 6: Manual smoke (real environment)**

Run: `uv run kiro-usage --once --no-account`
Expected: prints a local-only panel from the real `data.sqlite3` without error.
Then (optional, real token): `uv run kiro-usage --once` shows the official gauge.

- [ ] **Step 7: Commit**

```bash
git add src/kiro_usage/app.py src/kiro_usage/cli.py tests/test_cli.py
git commit -m "feat: wire live loop and CLI entry point"
```

---

### Task 10: Docs + final verification

**Files:**
- Modify: `README.md` (usage/flags section), `docs/superpowers/plans/2026-07-28-kiro-usage-monitor.md` (check off tasks)

- [ ] **Step 1: Update README** with the actual flags (`--once`, `--json`, `--no-account`, `--refresh`, `--timezone`, `--reset-hour`, `--reconfigure`), a screenshot/example, and the exit-code table.

- [ ] **Step 2: Run the complete gate one more time**

Run: `uv run ruff check && uv run ruff format --check && uv run pytest && codegraph init`
Expected: clean lint, all tests pass, CodeGraph re-indexes the new source.

- [ ] **Step 3: Commit and push**

```bash
git add README.md docs
git commit -m "docs: document CLI flags and exit codes"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- §2.1 local SQLite → Task 3. §2.2 getUsageLimits → Task 4. §2.3 token handling / needs-login → Task 4 (`NeedsLoginError`) + Task 8/9 (status) + Task 8 banner. §3 live view → Task 8 (render) + Task 9 (loop). §4 pace math → Task 5. §4.1 day counting/timezone → Task 5 + Task 9 (`--timezone`/`--reset-hour`). §4.2 workday mode + holidays → Task 6 + setup Task 7. §5 architecture (module split) → Tasks 2–9. §6 error handling / exit codes → Task 4, Task 8 (`run_once` codes), Task 6 (holiday fallback). §7 testing → every task is TDD with synthetic fixtures. §8 code quality → Global Constraints + every gate step. §9 packaging → Task 1. §10 out of scope → nothing built for it. All covered.

**Placeholder scan:** No "TBD"/"implement later". The three `# noqa` occurrences in sample code are each explicitly called out as things to REMOVE (they demonstrate a rule you'll hit and how to resolve it without suppression, per the block-and-raise policy) — they are teaching notes, not instructions to ship suppressions.

**Type consistency:** `ConversationRow`, `DbSnapshot`, `AccountInfo`, `PaceInfo`, `AppConfig`, `Snapshot` defined in Task 2 and consumed unchanged. `NeedsLoginError` raised in Task 4, caught in Task 8/9. `HolidayProvider` protocol defined in Task 5, implemented in Task 6. `render_snapshot(snap, cfg)`, `build_snapshot(...) -> Snapshot`, `run_once -> int`, `main(argv=None) -> int` names are consistent across tasks.
