# kiro-usage

A small, privacy-first terminal monitor for [Kiro CLI](https://kiro.dev)
credit usage. It reads Kiro's local SQLite data for live spend and (optionally)
calls Kiro's account endpoint for your official plan limit, then shows how your
consumption tracks against your billing cycle — with calendar-day or working-day
pacing.

> Status: in design / early development. See
> [`docs/superpowers/specs`](docs/superpowers/specs/) for the design.

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

## Development

```sh
uv sync
ruff check
ruff format --check
pytest
```

Lint runs with `ruff` `select = ["ALL"]` and no per-file/`# noqa` exceptions;
see the design doc for the code-quality policy.
