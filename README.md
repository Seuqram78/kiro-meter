# kiro-usage

A personal, unofficial live terminal monitor for [Kiro CLI](https://kiro.dev)
credit usage. It reads Kiro's local data for live spend and shows how your
consumption tracks against your billing cycle — with a plan gauge, pacing, and a
per-folder/model breakdown.

## What this is

I built this **for my own needs** and I'm sharing it as-is. It is **not
affiliated with Kiro or AWS**. If you want it to behave differently, please
**fork it or open a PR** — maintenance is best-effort and feature requests may
not be picked up.

## Scope

Works against the **Kiro CLI** only. It reads the Kiro CLI's local data on your
machine. It does **not** support the Kiro **IDE** (different data location and
mechanism). CLI today; IDE is not covered.

## What it shows

- **Plan gauge** — credits used vs your plan limit and the reset date, coloured
  green → amber → red by usage (official, when a valid Kiro session exists).
- **Today** — today's credit spend against your daily allowance.
- **Pace** — `allowance` (even daily budget = limit ÷ cycle length) vs
  `can spend` (remaining ÷ days left).
- **Burn rate** — recent credits per minute.
- **Usage by folder & model** — a bar chart of where this cycle's credits went.
- **Live meter** — a "next reading" countdown so you can see the auto-refresh.

Every number is labelled by provenance: **`official`** (from Kiro's account
endpoint) or **`local`** (derived from local data). If the Kiro session is
expired, the monitor keeps running on local data and prompts you to re-login.

## Install

Installed straight from this repo with [uv](https://docs.astral.sh/uv/) — there
is no PyPI package (the name `kiro-usage` on PyPI is an unrelated project).

```sh
# install the command
uv tool install git+https://github.com/Seuqram78/kiro-usage

# run without installing
uvx --from git+https://github.com/Seuqram78/kiro-usage kiro-usage

# pin to a release
uv tool install git+https://github.com/Seuqram78/kiro-usage@v26.7.0
```

Needs **Python 3.14** (uv will provision it automatically).

### Updating (manual — it does not auto-update)

A git install pins to the commit resolved at install time; new commits on `main`
do not update you until you ask:

```sh
uv tool upgrade kiro-usage
# or force the latest main if upgrade doesn't move (cached revision):
uv tool install --force git+https://github.com/Seuqram78/kiro-usage
```

A `@v26.7.0`-pinned install won't cross the tag; install a newer tag to move.
Releases use calendar versioning: `YY.M.PATCH` (e.g. `26.7.0` = 2026-07, patch 0).

On first run it asks whether to pace against working days (and, if so, your
country/region for public holidays, suggested from a list) and saves the answer
to `~/.kiro-usage/config.toml`.

## Usage

```sh
kiro-usage                 # live monitor (Ctrl-C to quit)
kiro-usage --once          # print one snapshot and exit
kiro-usage --json          # machine-readable snapshot (implies --once)
kiro-usage --no-account    # local data only; skip the official-limit call
```

| Flag | Effect |
|------|--------|
| `--once` | Print a single snapshot and exit instead of the live view. |
| `--json` | Emit the snapshot as JSON (implies `--once`). |
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

## How it works (and how auth is handled)

- **Spend** is read from Kiro CLI's **local SQLite database**
  (`~/.local/share/kiro-cli/data.sqlite3`), opened **read-only**.
- **The official plan limit** is fetched by **reusing the bearer token that
  kiro-cli already stored** (in its `auth_kv` table) to call an **undocumented**
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

It depends on **undocumented internals** of the Kiro CLI: the local SQLite
schema, the token-store format, and the `getUsageLimits` request/response shape.
**Any Kiro CLI update can change these and break the tool** — either silently
(wrong numbers) or loudly (errors).

It was built against the Kiro CLI as observed on **2026-07-28**, and there is
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
