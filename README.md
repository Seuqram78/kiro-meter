# kiro-meter

A personal, unofficial TUI (terminal UI) for visualizing [Kiro CLI](https://kiro.dev)
credit usage, meant to be run and read directly in a terminal. It reads Kiro's
local data for live spend and shows how your consumption tracks against your
billing cycle — with a plan gauge, pacing, and a per-folder/model breakdown.

## What this is

I built this **for my own needs** and I'm sharing it as-is. It is **not
affiliated with Kiro or AWS**. If you want it to behave differently, please
**open a PR** — maintenance is best-effort and feature requests may not be
picked up. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Scope

Works against **Kiro** (AWS's tool) only — both CLI and IDE are in scope, though
only the CLI is supported today (the IDE uses a different data location and
mechanism). Other AI tools/vendors are out of scope; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## What it shows

- **Plan gauge** — credits used vs your plan limit and the reset date, coloured
  green → amber → red by usage (official, when a valid Kiro session exists).
- **Today** — today's credit spend against your daily allowance.
- **Pace** — `allowance` (even daily budget = limit ÷ cycle length) vs
  `can spend` (remaining ÷ days left).
- **Burn rate** — recent credits per minute.
- **Usage by folder & model** — a bar chart of where this cycle's credits went,
  every folder/model pair with a total row.
- **Live meter** — a "next reading" countdown so you can see the auto-refresh.

Every number is labelled by provenance: **`official`** (from Kiro's account
endpoint) or **`local`** (derived from local data). If the Kiro session is
expired, the monitor keeps running on local data and prompts you to re-login.

## Install

Installed straight from this repo with [uv](https://docs.astral.sh/uv/) — it is
not published to PyPI; you install it directly from the Git repository.

```sh
# install the command
uv tool install git+https://github.com/Seuqram78/kiro-meter

# run without installing
uvx --from git+https://github.com/Seuqram78/kiro-meter kiro-meter

# pin to a release
uv tool install git+https://github.com/Seuqram78/kiro-meter@v26.7.0
```

Needs **Python 3.14** (uv will provision it automatically).

### Updating (manual — it does not auto-update)

A git install pins to the commit resolved at install time; new commits on `main`
do not update you until you ask:

```sh
uv tool upgrade kiro-meter
# or force the latest main if upgrade doesn't move (cached revision):
uv tool install --force git+https://github.com/Seuqram78/kiro-meter
```

A `@v26.7.0`-pinned install won't cross the tag; install a newer tag to move.
Releases use calendar versioning: `YY.M.PATCH` (e.g. `26.7.0` = 2026-07, patch 0).

On first run it asks whether to pace against working days (and, if so, your
country/region for public holidays, suggested from a list) and saves the answer
to `~/.kiro-meter/config.toml`.

## Usage

```sh
kiro-meter                 # live monitor (Ctrl-C to quit)
kiro-meter --once          # print one snapshot and exit
kiro-meter --json          # machine-readable snapshot (implies --once)
kiro-meter --no-account    # local data only; skip the official-limit call
```

| Flag | Effect |
|------|--------|
| `--once` | Print a single snapshot and exit instead of the live view. |
| `--json` | Emit the snapshot as JSON (implies `--once`) — see [JSON schema](#json-schema---json) below. |
| `--no-account` | Skip the account call; render local spend only. |
| `--refresh N` | Live refresh interval in seconds. |
| `--timezone TZ` | Timezone for the "today" boundary (e.g. `America/Sao_Paulo`). |
| `--reconfigure` | Re-run the first-time setup. |

### Exit codes (`--once`)

| Code | Meaning |
|------|---------|
| `0` | OK — below the near-limit threshold. |
| `10` | Near the plan limit (≥ 90%). |
| `11` | At or over the plan limit. |
| `20` | Indeterminate — no official limit available. |
| `30` | Error fetching the official limit. |

### JSON schema (`--json`)

`--json` prints the full snapshot as one compact line — full parity with what the
live view renders, structured for scripts and AI agents rather than a terminal.
Pretty-printed here for readability:

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-29T12:00:00+00:00",
  "account_status": "ok",
  "account": {
    "email": "user@example.com",
    "tier": "KIRO FREE",
    "sub_type": "FREE",
    "used": 11.21,
    "limit": 50.0,
    "currency": "USD",
    "next_reset": "2026-08-01T00:00:00+00:00",
    "overage_used": 0.0,
    "overage_cap": 10000.0,
    "overage_enabled": false
  },
  "today": { "credits": 0.31, "turns": 18 },
  "burn_rate_per_min": 0.02,
  "pace": {
    "mode": "calendar",
    "allowance_per_day": 1.61,
    "can_spend_per_day": 11.37,
    "today_fraction": 0.15,
    "days_until_reset": 4.0,
    "days_elapsed": 27.0,
    "projection_runout": null,
    "non_working_today": false,
    "holidays_available": true
  },
  "usage": {
    "scope": "this cycle",
    "by_folder_model": [
      ["/home/me/proj-a", "sonnet-4.5", 8, 0.21],
      ["/home/me/proj-b", "haiku-4.5", 12, 0.10]
    ],
    "total_credits": 0.31,
    "total_turns": 20
  }
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `schema_version` | integer | Increments on any breaking change to this shape; check it defensively. |
| `generated_at` | string (ISO-8601) | When this snapshot was taken. |
| `account_status` | string | `ok`, `needs_login`, `disabled`, or `error` — see exit codes above. |
| `account` | object or `null` | `null` unless `account_status` is `ok`. |
| `account.used` / `.limit` | number | Official credits used vs. plan limit. |
| `account.tier` / `.sub_type` | string | Plan name and subscription type. |
| `account.next_reset` | string (ISO-8601) | Start of the next billing cycle. |
| `account.overage_used` / `.overage_cap` / `.overage_enabled` | number / number / boolean | Overage state, if enabled. |
| `today.credits` / `.turns` | number / integer | Today's local spend (always present). |
| `burn_rate_per_min` | number or `null` | Recent local credits/minute. |
| `pace` | object or `null` | `null` whenever `account` is `null`. |
| `pace.mode` | string | `calendar` or `workday`. |
| `pace.allowance_per_day` / `.can_spend_per_day` | number or `null` | Even-budget vs. rest-of-cycle daily pace. |
| `pace.days_until_reset` / `.days_elapsed` | number | Days left/elapsed in the current cycle. |
| `pace.projection_runout` | string (ISO-8601) or `null` | Projected credit-exhaustion date, if trending over. |
| `usage` | object or `null` | `null` when there's no local spend data yet. |
| `usage.scope` | string | `"this cycle"` when `account` is present, else `"recent"`. |
| `usage.by_folder_model` | array of `[folder, model, turns, credits]` | One row per folder/model pair, all of them (not truncated). Array-of-arrays rather than objects to avoid repeating 4 key names per row — cheaper in tokens for a script or AI agent parsing many rows. Field order is fixed as documented here. |
| `usage.total_credits` / `.total_turns` | number / integer | Sum across every row in `by_folder_model`. |

## How it works (and how auth is handled)

- **Spend** is read from Kiro CLI's **local session files**
  (`~/.kiro/sessions/cli/*.json`, one per conversation), opened **read-only**.
  Each turn's credit cost lives at
  `session_state.conversation_metadata.user_turn_metadatas[*].metering_usage`.
- **The official plan limit** is fetched by **reusing the bearer token that
  kiro-cli already stored** (in the `auth_kv` table of its local SQLite database,
  `~/.local/share/kiro-cli/data.sqlite3`) to call an **undocumented**
  AWS/CodeWhisperer endpoint (`getUsageLimits`).
- It **never logs you in and never refreshes tokens** — kiro-cli owns the entire
  auth lifecycle. Whatever you logged into kiro-cli with (Google/GitHub social,
  AWS Builder ID, or IAM Identity Center) simply works, because the tool only
  reuses the token kiro-cli put there. If the token is expired, the tool shows a
  banner asking you to run `kiro-cli` (or `kiro-cli user login`) and keeps
  rendering local spend in the meantime.

Nothing is sent anywhere except that one authenticated call to Kiro's own
account endpoint. No telemetry.

## ⚠️ This is a fragile implementation

It depends on **undocumented internals** of the Kiro CLI: the local session-file
JSON schema, the SQLite token-store format, and the `getUsageLimits`
request/response shape. **Any Kiro CLI update can change these and break the
tool** — either silently (wrong numbers) or loudly (errors).

It was built against the Kiro CLI as observed on **2026-07-29**, and there is
**no guarantee** it keeps working as Kiro evolves. If it breaks, that is
expected — please open an issue or, better, a PR.

## Contributing

Forks and pull requests are welcome. This is a personal project, so reviews and
releases are best-effort.

## License

[MIT](LICENSE).

## Development

Requires Python 3.14 (pinned via `.python-version`).

```sh
uv sync
codegraph init      # once per clone — indexes the repo for CodeGraph
uv run ruff check
uv run ruff format --check
uv run pytest
```

- Lint runs with `ruff` `select = ["ALL"]` and no inline `# noqa` — see
  [the design doc](docs/superpowers/specs/) for the code-quality policy.
- The repo is a **CodeGraph** project — use `codegraph explore` to navigate the
  code. The index (`.codegraph/`) is git-ignored and regenerated locally.
