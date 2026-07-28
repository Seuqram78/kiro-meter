"""Tests for the shared dataclasses."""

from datetime import UTC, datetime

from kiro_meter.models import AccountInfo, AppConfig, ConversationRow

_EXPECTED_CREDITS = 0.5
_DEFAULT_REFRESH_SECONDS = 3


def test_conversation_row_is_frozen() -> None:
    """ConversationRow holds the parsed conversation fields."""
    row = ConversationRow("c1", "/proj", "haiku", _EXPECTED_CREDITS, 1000)
    assert row.credits == _EXPECTED_CREDITS


def test_appconfig_defaults() -> None:
    """AppConfig has sensible defaults."""
    cfg = AppConfig()
    assert cfg.refresh_seconds == _DEFAULT_REFRESH_SECONDS
    assert cfg.use_account is True
    assert cfg.workdays is False


def test_account_info_holds_reset_datetime() -> None:
    """AccountInfo carries a timezone-aware reset datetime."""
    info = AccountInfo(
        email="user@example.com",
        tier="KIRO FREE",
        sub_type="FREE",
        used=11.21,
        limit=50.0,
        overage_used=0.0,
        overage_cap=10000.0,
        overage_rate=0.04,
        overage_enabled=False,
        next_reset=datetime(2026, 8, 1, tzinfo=UTC),
        days_until_reset_api=0,
        currency="USD",
        fetched_at=datetime(2026, 7, 28, tzinfo=UTC),
    )
    assert info.next_reset.tzinfo is UTC
