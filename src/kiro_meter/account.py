"""Fetch official plan usage from the Kiro getUsageLimits endpoint."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kiro_meter.models import AccountInfo

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

_TOKEN_KEYS = (
    "kirocli:social:token",  # social login (Google/GitHub/Microsoft)
    "kirocli:odic:token",  # AWS SSO OIDC (Builder ID / Identity Center)
    "codewhisperer:odic:token",  # legacy AWS SSO OIDC
)
_PROFILE_STATE_KEY = "api.codewhisperer.profile"
_DEFAULT_REGION = "us-east-1"
_ARN_REGION = re.compile(r"arn:aws:\w+:([a-z0-9-]+):")
_SUBSECOND = re.compile(r"(\.\d{6})\d+")
_URL_TEMPLATE = "https://q.{region}.amazonaws.com/getUsageLimits"
_HEADERS = {"x-amzn-kiro-agent-mode": "vibe"}
_TIMEOUT_SECONDS = 15.0
_HTTP_FORBIDDEN = 403
_FEATURE_UNSUPPORTED = "FEATURE_NOT_SUPPORTED"
_AUTH_MARKERS = ("bearer token", "invalid_grant", "invalidtoken")
_CREDIT_RESOURCE = "CREDIT"
_OVERAGE_ENABLED = "ENABLED"
_ATTEMPTS = (
    {"resourceType": "AGENTIC_REQUEST", "origin": "AI_EDITOR"},
    {"origin": "AI_EDITOR"},
    {"resourceType": "CONVERSATION", "origin": "AI_EDITOR"},
)


class NeedsLoginError(Exception):
    """Raised when the stored token is rejected and a fresh login is required."""


@dataclass(frozen=True)
class KiroToken:
    """A Kiro CLI bearer token read from the local auth store.

    Covers both social login and AWS SSO OIDC (Builder ID / Identity Center).
    """

    access_token: str
    refresh_token: str
    profile_arn: str
    expires_at: datetime
    region: str


def load_token(db_path: Path) -> KiroToken | None:
    """Load the Kiro CLI bearer token from the ``auth_kv`` table.

    Supports social login and AWS SSO OIDC (Builder ID / Identity Center). For
    OIDC the profile ARN is usually absent from the token itself and is read
    from the ``state`` table instead.

    Args:
        db_path: Path to the Kiro CLI ``data.sqlite3`` file.

    Returns:
        The parsed token, or ``None`` if no usable token is stored.
    """
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        data = _first_token(conn)
        if data is None or "access_token" not in data:
            return None
        raw_arn = data.get("profile_arn")
        arn = raw_arn if isinstance(raw_arn, str) and raw_arn else _profile_arn(conn)
    finally:
        conn.close()
    if not arn:
        return None
    refresh = data.get("refresh_token")
    return KiroToken(
        access_token=str(data["access_token"]),
        refresh_token=refresh if isinstance(refresh, str) else "",
        profile_arn=arn,
        expires_at=_parse_expiry(data.get("expires_at")),
        region=_region_from_arn(arn),
    )


def _first_token(conn: sqlite3.Connection) -> dict[str, object] | None:
    """Return the first token payload found across the known auth keys."""
    for key in _TOKEN_KEYS:
        row = conn.execute("SELECT value FROM auth_kv WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return json.loads(row[0])
    return None


def _profile_arn(conn: sqlite3.Connection) -> str | None:
    """Read the CodeWhisperer profile ARN from the ``state`` table."""
    row = conn.execute(
        "SELECT value FROM state WHERE key = ?", (_PROFILE_STATE_KEY,)
    ).fetchone()
    if row is None:
        return None
    arn = json.loads(row[0]).get("arn")
    return arn if isinstance(arn, str) else None


def _parse_expiry(value: object) -> datetime:
    """Parse an ISO-8601 expiry, trimming sub-microseconds; far future if absent."""
    if not isinstance(value, str):
        return datetime.max.replace(tzinfo=UTC)
    return datetime.fromisoformat(_SUBSECOND.sub(r"\1", value))


def _region_from_arn(profile_arn: str) -> str:
    """Extract the AWS region from a profile ARN, defaulting to us-east-1."""
    match = _ARN_REGION.search(profile_arn)
    return match.group(1) if match else _DEFAULT_REGION


def token_expired(token: KiroToken, now: datetime) -> bool:
    """Return whether the token is at or past its expiry."""
    return now >= token.expires_at


def fetch_account_info(
    token: KiroToken,
    *,
    client: httpx.Client,
    now: datetime,
) -> AccountInfo:
    """Call getUsageLimits and parse the official usage into an AccountInfo.

    Args:
        token: The bearer token to authenticate with.
        client: An httpx client (injected so tests can mock transport).
        now: The current instant, recorded as ``fetched_at``.

    Returns:
        The parsed official account usage.

    Raises:
        NeedsLoginError: If the token is rejected, or every parameter
            combination reports the feature unsupported.
    """
    url = _URL_TEMPLATE.format(region=token.region)
    auth = {"Authorization": f"Bearer {token.access_token}", **_HEADERS}
    for attempt in _ATTEMPTS:
        params = {"isEmailRequired": "true", "profileArn": token.profile_arn, **attempt}
        response = client.get(
            url, params=params, headers=auth, timeout=_TIMEOUT_SECONDS
        )
        if _is_auth_failure(response):
            message = "Kiro session rejected; re-login required"
            raise NeedsLoginError(message)
        if _FEATURE_UNSUPPORTED in response.text:
            continue
        response.raise_for_status()
        return _parse(response.json(), now)
    message = "getUsageLimits unsupported for all parameter combinations"
    raise NeedsLoginError(message)


def _is_auth_failure(response: httpx.Response) -> bool:
    """Detect a 403 or an auth-related error body."""
    if response.status_code == _HTTP_FORBIDDEN:
        return True
    body = response.text.lower()
    return any(marker in body for marker in _AUTH_MARKERS)


def _parse(data: dict[str, object], now: datetime) -> AccountInfo:
    """Map a getUsageLimits response body to an AccountInfo."""
    items = data.get("usageBreakdownList") or []
    credit = _credit_item(items)
    subscription = _as_dict(data.get("subscriptionInfo"))
    overage = _as_dict(data.get("overageConfiguration"))
    user = _as_dict(data.get("userInfo"))
    return AccountInfo(
        email=str(user.get("email", "")),
        tier=str(subscription.get("subscriptionTitle", "Unknown")),
        sub_type=str(subscription.get("type", "")),
        used=_pick(credit, "currentUsageWithPrecision", "currentUsage"),
        limit=_pick(credit, "usageLimitWithPrecision", "usageLimit"),
        overage_used=_pick(credit, "currentOveragesWithPrecision", "currentOverages"),
        overage_cap=_pick(credit, "overageCapWithPrecision", "overageCap"),
        overage_rate=float(credit.get("overageRate", 0.0)),
        overage_enabled=overage.get("overageStatus") == _OVERAGE_ENABLED,
        next_reset=datetime.fromtimestamp(
            float(data.get("nextDateReset", 0.0)), tz=UTC
        ),
        days_until_reset_api=int(data.get("daysUntilReset", 0)),
        currency=str(credit.get("currency", "USD")),
        fetched_at=now,
    )


def _credit_item(items: list[dict[str, object]]) -> dict[str, object]:
    """Return the CREDIT breakdown item, or the first item, or an empty dict."""
    for item in items:
        if item.get("resourceType") == _CREDIT_RESOURCE:
            return item
    return items[0] if items else {}


def _as_dict(value: object) -> dict[str, object]:
    """Return ``value`` if it is a dict, else an empty dict."""
    return value if isinstance(value, dict) else {}


def _pick(item: dict[str, object], precise_key: str, fallback_key: str) -> float:
    """Prefer the precise field, falling back to the integer field, then zero."""
    return float(item.get(precise_key, item.get(fallback_key, 0.0)))
