"""Tests for the account/getUsageLimits client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from kiro_usage.account import (
    NeedsLoginError,
    SocialToken,
    fetch_account_info,
    token_expired,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)

_EXPECTED_USED = 11.21
_EXPECTED_LIMIT = 50.0
_EXPECTED_RATE = 0.04
_FIRST_CALL = 1
_SECOND_CALL = 2
_FAKE_ACCESS = "test-access"
_FAKE_REFRESH = "test-refresh"


def _token() -> SocialToken:
    return SocialToken(
        access_token=_FAKE_ACCESS,
        refresh_token=_FAKE_REFRESH,
        profile_arn="arn:aws:codewhisperer:us-east-1:1:profile/X",
        expires_at=datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
        region="us-east-1",
    )


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_token_expired() -> None:
    """A token past its expiry is reported expired."""
    later = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
    assert token_expired(_token(), later) is True
    assert token_expired(_token(), _NOW) is False


def test_fetch_parses_official_fields(usage_response: dict[str, object]) -> None:
    """A 200 response maps to AccountInfo with precise values."""
    client = _mock_client(lambda _r: httpx.Response(200, json=usage_response))
    info = fetch_account_info(_token(), client=client, now=_NOW)
    assert info.used == _EXPECTED_USED
    assert info.limit == _EXPECTED_LIMIT
    assert info.tier == "KIRO FREE"
    assert info.overage_rate == _EXPECTED_RATE
    assert info.email == "user@example.com"
    assert info.next_reset.tzinfo is UTC


def test_fetch_403_raises_needs_login() -> None:
    """A 403 becomes NeedsLoginError."""
    client = _mock_client(lambda _r: httpx.Response(403, text="bearer token invalid"))
    with pytest.raises(NeedsLoginError):
        fetch_account_info(_token(), client=client, now=_NOW)


def test_fetch_retries_on_feature_not_supported(
    usage_response: dict[str, object],
) -> None:
    """A FEATURE_NOT_SUPPORTED body triggers the next param combo."""
    calls = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == _FIRST_CALL:
            return httpx.Response(400, text="FEATURE_NOT_SUPPORTED")
        return httpx.Response(200, json=usage_response)

    info = fetch_account_info(_token(), client=_mock_client(handler), now=_NOW)
    assert calls["n"] == _SECOND_CALL
    assert info.used == _EXPECTED_USED
