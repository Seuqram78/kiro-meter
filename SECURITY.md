# Security Policy

kiro-meter is a solo-maintained, best-effort project (see [README](README.md)).
This policy sets expectations accordingly — there's no SLA, but reports are
taken seriously and triaged as soon as possible.

## Supported versions

Only the latest release is supported. Given the calendar-versioning scheme
(`YY.M.PATCH`) and best-effort maintenance, older versions do not receive
backported fixes — please upgrade before reporting.

## What this tool touches

For context when assessing impact:

- **Reads**, read-only, the Kiro CLI's local SQLite auth store to obtain your
  existing bearer token (`account.py`). It never writes to that store and
  never generates or stores credentials of its own.
- **Sends** that token to a single first-party endpoint —
  `https://q.{region}.amazonaws.com/getUsageLimits` — over HTTPS, to fetch
  your official plan usage. It is not sent anywhere else.
- Local config (pacing preferences, timezone, etc.) is stored in
  `~/.kiro-meter/config.toml`. No telemetry, analytics, or third-party
  services are involved.

A vulnerability that would leak, log, or exfiltrate that token — or any other
local data the tool reads — to somewhere other than the endpoint above is in
scope. Vulnerabilities in Kiro CLI, the Kiro/AWS backend, or `uv` itself are
out of scope here; please report those upstream.

## Reporting a vulnerability

Please **do not open a public GitHub issue** for security reports.

Email **romadasilva@protonmail.com** with:

- A description of the issue and its potential impact.
- Steps to reproduce (a minimal repro is very helpful).
- The kiro-meter version and OS you tested on.

You should get an acknowledgement within a few days. If a fix is needed,
it'll ship as a new patch release; you'll be credited in the release notes
unless you'd prefer otherwise.
