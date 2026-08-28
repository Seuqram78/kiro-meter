# Repo-specific instructions

## Git workflow

Commit directly to `main`. Do **not** create a feature branch before
committing, even if general guidance says to branch off the default branch
first — that convention does not apply to this repo. Push to `origin/main`
directly once the user asks for a commit/push.

## Linting

`./lint.sh` is **the** linting standard and the single entry point. Run it
before every commit and treat a non-zero exit as a blocker.

Do not substitute the individual tools (`uv run ruff check`, `ruff format
--check`, `complexipy`, `pip-audit`) — they are subsets of what `lint.sh`
runs, so a green `ruff check` says nothing about the other gates.

Do not widen the ruff configuration to make a check pass. `select = ["ALL"]`,
the top-level `ignore` list, and `per-file-ignores` are all gated by
`tests/test_code_quality.sh`, which fails on anything outside its sanctioned
baseline, and inline `# noqa` is banned outright. If a rule genuinely cannot
be satisfied, restructure the code; if that fails, raise it with the
maintainer rather than adding an escape hatch.

`uv run pytest` is a separate gate, not part of `lint.sh`. Run both.
