"""Smoke tests for package import and version."""

from kiro_usage import __version__


def test_version_is_a_string() -> None:
    """The package exposes a string version."""
    assert isinstance(__version__, str)
    assert __version__
