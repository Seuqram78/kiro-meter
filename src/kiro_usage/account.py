"""Fetch official plan usage from the Kiro getUsageLimits endpoint."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from kiro_usage.models import AccountInfo

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

_AUTH_ROW = "kirocli:social:token"
_DEFAULT_REGION = "us-east-1"
_ARN_REGION = re.compile(r"arn:aws:\w+:([a-z0-9-]+):")
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
class SocialToken:
    """A Kiro CLI bearer token read from the local auth store."""

    access_token: str
    refresh_token: str
    profile_arn: str
    expires_at: datetime
    region: str


def load_token(db_path: Path) -> SocialToken | None:
    """Load the social token from the Kiro CLI ``auth_kv`` table.

    Args:
        db_path: Path to the Kiro CLI ``data.sqlite3`` file.

    Returns:
        The parsed token, or ``None`` if no token row exists.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM auth_kv WHERE key = ?",
            (_AUTH_ROW,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    data = json.loads(row[0])
    return SocialToken(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        profile_arn=data["profile_arn"],
        expires_at=datetime.fromisoformat(data["expires_at"]),
        region=_region_from_arn(data["profile_arn"]),
    )


def _region_from_arn(profile_arn: str) -> str:
    """Extract the AWS region from a profile ARN, defaulting to us-east-1."""
    match = _ARN_REGION.search(profile_arn)
    return match.group(1) if match else _DEFAULT_REGION


def token_expired(token: SocialToken, now: datetime) -> bool:
    """Return whether the token is at or past its expiry."""
    return now >= token.expires_at


def fetch_account_info(
    token: SocialToken,
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
