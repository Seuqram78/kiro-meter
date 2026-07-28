# kiro-usage — live usage monitor for Kiro CLI

**Status:** Design (approved for planning)
**Date:** 2026-07-28
**Author:** Rodrigo Marques

## 1. Purpose

A small, privacy-first terminal monitor that shows live Kiro CLI credit
consumption and how it tracks against your plan. Inspired by
[Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor),
but scoped down and adapted to Kiro's data.

Distributed as a **uv tool** (`uv tool install` / `uvx`), entry point
`kiro-usage`.

## 2. Data sources

Two clearly separated sources, each with an explicit provenance label in the UI.

### 2.1 Local SQLite (authoritative for *spend*)

File: `~/.local/share/kiro-cli/data.sqlite3` (read-only).

| Datum | Location |
|---|---|
| Per-turn credit cost | `conversations_v2.value` → JSON `user_turn_metadata.usage_info[].value` (unit `credit`) |
| Working directory (folder) | `conversations_v2.key` column, and per-turn `history[].user.env_context.env_state.current_working_directory` |
| Model per request | JSON `history[].request_metadata.model_id` |
| Token counts (often null) | `history[].request_metadata.{total,uncached_input,output,cache_read,cache_write}_tokens` |
| Context usage % | `history[].request_metadata.context_usage_percentage` |
| Timestamps | `conversations_v2.updated_at` (ms), `history[].request_metadata.request_start_timestamp_ms` |

Provenance: **local**. Known caveat: credit cost is stored at
turn-metadata level; if Kiro persists only the latest turn's value per
conversation, "Today" is summed per-conversation and labelled `local · approx`.
Exact accumulation is verified when building `db.py`.

### 2.2 `GetUsageLimits` HTTP (authoritative for *plan limit*)

- Endpoint: `https://q.us-east-1.amazonaws.com/`, operation `GetUsageLimits`,
  header `Origin: KIRO_CLI` (AWS CodeWhisperer service).
- Auth: bearer token read from `data.sqlite3` table `auth_kv`, key
  `kirocli:social:token` (fields `access_token`, `refresh_token`,
  `profile_arn`, `expires_at`). Reference implementations:
  [hueyexe/open-kiro](https://github.com/hueyexe/open-kiro),
  [chaogei/Kiro-account-manager](https://github.com/chaogei/Kiro-account-manager).
- Returns: used (precise + integer), limit (precise + integer), overage rate /
  cap / consumed, `daysUntilReset`, `nextDateReset`, tier, email.

Provenance: **official** (cached; UI shows "N min ago"; refreshed ~every 5 min).

### 2.3 Token handling — no refresh code

The tool **does not** implement token refresh. Kiro supports three auth methods
(Social, Builder ID, Identity Center) with different refresh flows; reproducing
them is fragile and untestable across methods. Instead:

1. Read whatever token is in `auth_kv`.
2. Check `expires_at`; if expired, or the HTTP call returns 401, show a banner
   instead of the plan gauge:
   `⚠ Kiro session expired — run kiro-cli (or kiro-cli user login) to refresh, then press r.`
3. Local SQLite panels keep rendering throughout — the monitor never blanks.

This is uniform across all auth methods because Kiro owns each refresh flow.

## 3. Live view

```
┌ Kiro Usage ─────────────────────────────── Pro · you@email ┐
│  Plan    ▓▓▓▓▓▓▓░░░░░░░░░  6.20 / 40.00 cr   15%           │
│          resets Aug 1 · in 5d          (official · 4m ago)  │
│  + overage  0.00 / 10.00 cr  @ $0.04/cr                     │
│                                                             │
│  Today   ▓▓▓▓▓░░░░░  0.31 cr        (local)                 │
│          82% of target pace                                 │
│  Pace    target  0.38 cr/day  (remaining 6.20 ÷ 16d left)   │
│          actual  0.45 cr/day  (used 13.80 ÷ 14d elapsed) ↑  │
│          → at actual pace, budget lasts ~13.8d (2d early)   │
│  Burn    0.021 cr/min  (last 15 min)                        │
│                                                             │
│  By folder            cr     By model             cr        │
│    proj-a          0.21       claude-sonnet-4.5  0.18        │
│    proj-b          0.08       claude-haiku-4.5   0.13        │
│                                                             │
│  Recent turns                                               │
│    14:32  proj-a   sonnet-4.5   0.017 cr   ctx 6.6%          │
└ [q]uit  [r]efresh · polling 3s · limit every 5m ───────────┘
```

| Element | Source | Provenance |
|---|---|---|
| Plan gauge (used/limit, %, reset + countdown) | `GetUsageLimits` | official |
| Overage (consumed/cap, rate) | `GetUsageLimits` | official; hidden for enterprise/non-overage |
| Today spend + bar | SQLite spend ÷ target pace | local ÷ official |
| Target / actual pace + projection | derived (see §4) | official-derived |
| Burn rate (cr/min, recent window) | SQLite | local |
| Folder / model breakdown | SQLite | local |
| Recent turns | SQLite | local |

The view leads with **credits**, not tokens (token fields are frequently null).

## 4. Pace math

- **Target pace** = `(LimitPrecise − UsedPrecise) ÷ days_until_reset` — cr/day to
  exactly last until reset. Denominator for the **Today** bar.
- **Actual pace** = `UsedPrecise ÷ days_elapsed` — uses the *official* cycle
  total so it is not undercounted by local DB pruning.
- `days_until_reset = (NextDateReset − now) / 86400` (fractional, from
  timestamps). `cycle_start` = one calendar month before `NextDateReset`;
  `days_elapsed = cycle_len − days_until_reset`.
- **Projection** = `remaining ÷ actual_pace`, mapped back to a calendar date.
- **Guards:** if `days_elapsed < 0.5` or `days_until_reset` rounds to 0, pace
  shows `—`. Never divide by zero.
- **Fallback:** when no official limit is available (expired token /
  `--no-account`), target/actual pace are hidden and the Today bar shows the
  bare number. Burn rate (local, needs no limit) still renders.

### 4.1 Day counting

Default is **calendar days** — Kiro's cap is a continuous calendar-time budget
that resets on a date and does not pause on weekends/holidays.

- *Cycle pace math* uses absolute UTC timestamps (timezone-independent).
- *"Today"'s midnight boundary* uses local timezone, with optional
  `--timezone` / `--reset-hour` overrides.

### 4.2 Workday mode (optional, interactive setup)

If enabled, pace is computed per **working day** (calendar days minus weekends
minus holidays). Both target and actual switch to a per-working-day basis so
they stay comparable; the projection still maps back to a calendar date.

- **Setup** (first run, persisted to `~/.kiro-usage/config.toml`, re-editable via
  `kiro-usage --reconfigure`): asks whether to pace against working days, and if
  so the **country** (auto-detected from locale as default) and an optional
  **region** (ISO 3166-2 subdivision suffix).
- **Holiday source:** [Nager.Date](https://date.nager.at) public API
  (`GET /api/v3/PublicHolidays/{year}/{CC}`), no API key. A holiday applies if
  its `global` flag is set or the configured region is in its `counties`.
- **Caching:** fetched lazily only when workday mode is on, only for the year(s)
  the cycle touches, cached permanently per year to
  `~/.kiro-usage/holidays-{CC}-{year}.json` (effectively one fetch per year).
- **Degradation:** if the API is unreachable and no cache exists, fall back to
  weekends-only working days with a visible `holidays unavailable` tag. Never
  blocks the monitor.
- **Edge case:** on a weekend/holiday in workday mode, the Today bar shows the
  bare number with a `non-working day` tag (no per-day target applies).

## 5. Architecture

Isolated, independently testable modules:

| Module | Responsibility | Depends on |
|---|---|---|
| `db.py` | Read-only SQLite queries → spend/turns/folder/model aggregates + burn rate. Pure, testable against a fixture DB. | stdlib `sqlite3` |
| `account.py` | Load token from `auth_kv`; one `GetUsageLimits` HTTP call → typed `AccountInfo` or `needs_login` error. No refresh logic. | `httpx` |
| `pace.py` | Target/actual pace, projection, calendar/workday day counting, holiday client + cache. Pure given inputs. | `httpx` (holidays), stdlib `datetime` |
| `config.py` | Load/save `~/.kiro-usage/config.toml`; first-run setup prompts. | stdlib `tomllib` + writer |
| `render.py` | Snapshot dict → `rich` renderable. No I/O. | `rich` |
| `app.py` | `rich.Live` loop: poll SQLite ~3s, refresh limit ~5min, handle keys. | above |
| `cli.py` | Entry point `kiro-usage`; flags `--refresh`, `--once`, `--no-account`, `--json`, `--timezone`, `--reset-hour`, `--reconfigure`. | above |

**Data flow:** `cli` → build config → `app` loop → each tick: `db` snapshot +
(cached) `account` + `pace` → merged snapshot dict → `render` → `rich.Live`.

## 6. Error handling

- Missing/locked SQLite DB → clear message ("Kiro CLI data not found at …").
- Expired token / 401 → login banner; local panels continue.
- Holiday API failure → weekends-only fallback with tag.
- All network calls time-bounded; failures degrade, never crash the loop.
- `--once` exit codes for scripting: `0` ok, `10` near limit, `11` at limit,
  `20` indeterminate (no official data), `30` error.

## 7. Testing

- `db.py`: unit tests against a checked-in fixture `data.sqlite3` (synthetic,
  no real tokens) covering spend aggregation, folder/model grouping, burn rate,
  the latest-turn-vs-ledger caveat.
- `pace.py`: pure unit tests for target/actual/projection, day-count guards,
  calendar vs workday, holiday filtering, degradation.
- `account.py`: HTTP mocked (`httpx` transport) for success, 401→needs_login,
  timeout.
- `render.py`: snapshot → string rendering, including fallback/banner states.
- No live network or real credentials in tests.

## 8. Code quality

- **Lint:** `ruff check` with `[tool.ruff.lint] select = ["ALL"]` + `ruff format`.
- **No escape hatches:** no inline `# noqa`, no `per-file-ignores`. Rules are
  satisfied by writing compliant code (resolved absolute path + fixed argv +
  `shell=False` for any subprocess; named constants for magic numbers; full type
  annotations and docstrings). When a rule genuinely cannot/should not be
  satisfied, **stop and raise to the maintainer** — do not suppress.
- **Only permitted ignores** — ruff's own internally contradictory rules, each
  documented inline:
  ```toml
  [tool.ruff.lint]
  select = ["ALL"]
  ignore = [
    "D203",   # conflicts with D211 (ruff docs)
    "D213",   # conflicts with D212 (ruff docs)
    "COM812", # conflicts with ruff format (ruff docs)
    "ISC001", # conflicts with ruff format (ruff docs)
  ]
  ```
- **Git:** small, incremental commits, each with `ruff check` + tests green
  before committing. Conventional-commit style messages.

## 9. Packaging

- `pyproject.toml`, entry point `[project.scripts] kiro-usage = "kiro_usage.cli:main"`.
- Runtime deps: `rich`, `httpx` (holiday + account calls); stdlib `sqlite3`,
  `tomllib`, `datetime`, `zoneinfo`.
- Python ≥ 3.11 (`tomllib`). Install: `uv tool install kiro-usage`; run: `uvx kiro-usage`.

## 10. Out of scope (deferred)

- Token refresh / re-login automation.
- Historical warehouse beyond Kiro's own retention.
- ML/P90 limit detection.
- Textual full-app UI (rich.Live is sufficient for a read-only monitor).
