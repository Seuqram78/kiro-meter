"""Load, save, and interactively collect kiro-usage configuration."""

from __future__ import annotations

import dataclasses
import locale
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from kiro_usage.models import AppConfig

if TYPE_CHECKING:
    from collections.abc import Callable

CONFIG_PATH: Path = Path.home() / ".kiro-usage/config.toml"

_YES_ANSWERS = {"y", "yes"}
_COUNTRY_CODE_LEN = 2


def load_config(path: Path = CONFIG_PATH) -> AppConfig | None:
    """Load configuration from ``path``.

    Args:
        path: Location of the TOML config file.

    Returns:
        The parsed config, or ``None`` if the file does not exist.
    """
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {field.name for field in dataclasses.fields(AppConfig)}
    filtered = {key: value for key, value in data.items() if key in known}
    return dataclasses.replace(AppConfig(), **filtered)


def save_config(cfg: AppConfig, path: Path = CONFIG_PATH) -> None:
    """Persist the non-default fields of ``cfg`` to ``path`` as TOML."""
    defaults = AppConfig()
    lines = [
        f"{field.name} = {_format_value(value)}"
        for field in dataclasses.fields(AppConfig)
        if (value := getattr(cfg, field.name)) != getattr(defaults, field.name)
        and value is not None
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _format_value(value: object) -> str:
    """Render a scalar config value as TOML."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return f'"{value}"'


def detect_country() -> str | None:
    """Best-effort two-letter country code from the system locale."""
    try:
        tag = locale.getlocale()[0]
    except ValueError, locale.Error:
        tag = None
    tag = tag or os.environ.get("LANG", "")
    region = tag.split(".", maxsplit=1)[0].split("_")
    if len(region) == _COUNTRY_CODE_LEN and len(region[1]) == _COUNTRY_CODE_LEN:
        return region[1].upper()
    return None


def _no_regions(_country: str) -> list[str]:
    """Default region lister that offers no suggestions."""
    return []


def run_first_time_setup(
    *,
    prompt: Callable[[str], str] = input,
    detect: Callable[[], str | None] = detect_country,
    list_regions: Callable[[str], list[str]] = _no_regions,
) -> AppConfig:
    """Interactively collect first-run configuration.

    Args:
        prompt: Function used to ask the user a question (injectable for tests).
        detect: Function returning the default country code.
        list_regions: Function returning the subdivision codes that have
            holidays for a country, used to suggest a region.

    Returns:
        The configuration chosen by the user.
    """
    answer = prompt("Pace against working days only? (skip weekends/holidays) [y/N]: ")
    if answer.strip().lower() not in _YES_ANSWERS:
        return AppConfig()
    default_country = detect()
    country = prompt(f"Country for holidays [{default_country or ''}]: ").strip()
    country = country or default_country
    return AppConfig(
        workdays=True,
        country=country,
        region=_choose_region(prompt, list_regions, country),
    )


def _choose_region(
    prompt: Callable[[str], str],
    list_regions: Callable[[str], list[str]],
    country: str | None,
) -> str | None:
    """Offer the country's holiday regions and return the chosen code."""
    if not country:
        return None
    regions = list_regions(country)
    if not regions:
        return None
    listing = "  ".join(
        f"{index}) {code}" for index, code in enumerate(regions, start=1)
    )
    answer = prompt(
        f"Regions with holidays:\n  {listing}\nPick a number (or Enter to skip): "
    ).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(regions):
        return regions[int(answer) - 1]
    return None
