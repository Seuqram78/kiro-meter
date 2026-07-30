# Contributing

kiro-meter is maintained best-effort by one person. PRs are welcome — please
open one rather than maintaining a long-lived fork, so fixes and improvements
land in one place.

## Scope

- **Kiro CLI and Kiro IDE** are both in scope. The tool currently only reads
  CLI data; IDE support (different data location and mechanism) is a welcome
  contribution.
- **Other AI tools/vendors** (Claude Code, other agent CLIs, etc.) are out of
  scope. This project reads Kiro-specific data formats only — it will not
  become a multi-vendor monitor.

## Local setup

```sh
uv sync
uv run kiro-meter          # run from source
```

Requires Python 3.14 (uv provisions it automatically).

## Before opening a PR

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run complexipy .
```

All four must pass. Follow the existing `ruff` config in `pyproject.toml`
(`select = ["ALL"]`, with documented exceptions) rather than introducing new
lint suppressions unless there's a good reason.

## Commits

Prefix commits with `feat:`, `fix:`, `docs:`, `refactor:`, or `test:`,
matching the existing log.

## Anything else

Be respectful in issues and PRs — off-topic or abusive ones will be closed
without discussion.
