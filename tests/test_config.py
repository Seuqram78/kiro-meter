"""Tests for config persistence and setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kiro_usage.config import load_config, run_first_time_setup, save_config
from kiro_usage.models import AppConfig

if TYPE_CHECKING:
    from pathlib import Path


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    """A saved config loads back equal."""
    path = tmp_path / "config.toml"
    cfg = AppConfig(workdays=True, country="BR", region="SP")
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_load_missing_returns_none(tmp_path: Path) -> None:
    """Absent config file yields None."""
    assert load_config(tmp_path / "nope.toml") is None


def test_setup_workdays_yes_collects_location() -> None:
    """Answering yes collects country and region."""
    answers = iter(["y", "BR", "SP"])
    cfg = run_first_time_setup(prompt=lambda _p: next(answers), detect=lambda: "BR")
    assert cfg.workdays is True
    assert cfg.country == "BR"
    assert cfg.region == "SP"


def test_setup_workdays_no_uses_calendar() -> None:
    """Answering no leaves calendar mode."""
    cfg = run_first_time_setup(prompt=lambda _p: "n", detect=lambda: "BR")
    assert cfg.workdays is False
    assert cfg.country is None
