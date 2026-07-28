# kiro-usage

A small, privacy-first terminal monitor for [Kiro CLI](https://kiro.dev)
credit usage. It reads Kiro's local SQLite data for live spend and (optionally)
calls Kiro's account endpoint for your official plan limit, then shows how your
consumption tracks against your billing cycle — with calendar-day or working-day
pacing.

> See [`docs/superpowers/specs`](docs/superpowers/specs/) for the design and
> [`docs/superpowers/plans`](docs/superpowers/plans/) for the implementation plan.

## What it shows

- **Plan gauge** — used vs plan limit, reset date, overage (official, when a
  valid Kiro session exists).
- **Today** — today's credit spend, filled against your target daily pace.
- **Pace** — target pace (to last the cycle) vs actual pace (what you're really
  averaging), with an early/late projection.
- **Burn rate** — recent credits-per-minute.
- **Breakdowns** — by folder and by model, plus recent turns.

All numbers are provenance-labelled (`official` vs `local`). If the Kiro session
is expired, the monitor keeps running on local data and prompts you to re-login
with `kiro-cli`.

## Install

```sh
uv tool install kiro-usage
# or run without installing:
uvx kiro-usage
```

On first run it asks whether to pace against working days (and, if so, your
country/region for public holidays) and saves the answer to
`~/.kiro-usage/config.toml`.

## Usage

```sh
kiro-usage                 # live monitor (Ctrl-C to quit)
kiro-usage --once          # print one snapshot and exit
kiro-usage --json          # machine-readable snapshot (implies --once)
kiro-usage --no-account    # local SQLite only; skip the official limit call
```

| Flag | Effect |
|------|--------|
| `--once` | Print a single snapshot and exit instead of the live view. |
| `--json` | Emit the snapshot as JSON (implies `--once`). |
| `--no-account` | Skip the `getUsageLimits` call; render local spend only. |
| `--refresh N` | Live refresh interval in seconds. |
| `--timezone TZ` | Timezone for the "today" boundary (e.g. `America/Sao_Paulo`). |
| `--reconfigure` | Re-run the first-time setup. |

The official plan limit is fetched with whatever Kiro token is already on the
machine — the tool never refreshes tokens itself. If the session is expired it
shows a banner asking you to run `kiro-cli` (or `kiro-cli user login`) and keeps
rendering local spend meanwhile.

### Exit codes (`--once`)

| Code | Meaning |
|------|---------|
| `0` | OK — below the near-limit threshold. |
| `10` | Near the plan limit (≥ 90%). |
| `11` | At or over the plan limit. |
| `20` | Indeterminate — no official limit available. |
| `30` | Error fetching the official limit. |

## Development

Requires Python 3.14 (pinned via `.python-version`).

```sh
uv sync
codegraph init      # once per clone — indexes the repo for CodeGraph
ruff check
ruff format --check
pytest
```

- Lint runs with `ruff` `select = ["ALL"]` and no per-file/`# noqa` exceptions;
  see the design doc for the code-quality policy.
- The repo is a **CodeGraph** project — use `codegraph explore` (or the
  `codegraph_explore` MCP tool) to navigate the code. The index (`.codegraph/`)
  is git-ignored and regenerated locally.
