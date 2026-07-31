"""Pure keyboard-driven UI state for the live view (not persisted)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

import readchar

from kiro_meter.db import FULL_NESTING

_MIN_NESTING = 1
# Terminals in "application cursor keys" mode (DECCKM) - common behind SSH
# clients like PuTTY - send arrows as SS3 sequences ("\x1bOA") instead of
# readchar's default CSI form ("\x1b[A"); recognize both so arrow keys work
# regardless of which mode the terminal happens to be in.
_SCROLL_DELTA = {readchar.key.UP: -1, "\x1bOA": -1, readchar.key.DOWN: 1, "\x1bOB": 1}
# left = collapse (less detail), right = expand (more detail)
_NESTING_DELTA = {
    readchar.key.LEFT: -1,
    "\x1bOD": -1,
    readchar.key.RIGHT: 1,
    "\x1bOC": 1,
}

TableSort = Literal["cr_desc", "cr_asc", "folder_asc", "folder_desc"]
"""Active sort order for the usage table.

Cycles through four states via the ``s`` key:
``cr_desc`` (default) → ``cr_asc`` → ``folder_asc`` → ``folder_desc`` → …
"""

_SORT_CYCLE: tuple[TableSort, ...] = (
    "cr_desc",
    "cr_asc",
    "folder_asc",
    "folder_desc",
)


@dataclass(frozen=True)
class LiveState:
    """Scroll position, folder nesting, local-visibility, and sort for one run.

    Reset to defaults every launch - this is deliberately not persisted to
    ``~/.kiro-meter/config.toml``.
    """

    nesting: int = FULL_NESTING
    scroll: int = 0
    show_local: bool = True
    quit: bool = False
    sort: TableSort = "cr_desc"


def apply_key(state: LiveState, key: str) -> LiveState:
    """Map one keypress to a new state.

    Only enforces the static invariants (``scroll >= 0``, ``nesting >= 1``);
    clamping nesting to what the current data actually supports is
    ``normalize``'s job, since that depends on data this function doesn't see.
    """
    if key in _SCROLL_DELTA:
        return replace(state, scroll=max(0, state.scroll + _SCROLL_DELTA[key]))
    if key in _NESTING_DELTA:
        nesting = max(_MIN_NESTING, state.nesting + _NESTING_DELTA[key])
        return replace(state, nesting=nesting)
    if key == "l":
        return replace(state, show_local=not state.show_local)
    if key == "s":
        idx = _SORT_CYCLE.index(state.sort)
        return replace(state, sort=_SORT_CYCLE[(idx + 1) % len(_SORT_CYCLE)])
    if key in ("q", readchar.key.CTRL_C):
        return replace(state, quit=True)
    return state


def normalize(state: LiveState, *, max_nesting: int) -> LiveState:
    """Clamp nesting to the real depth of the current data.

    Run every tick so a Left-press always decrements from a real, meaningful
    number instead of the ``FULL_NESTING`` sentinel.
    """
    bound = max(max_nesting, _MIN_NESTING)
    return replace(state, nesting=max(_MIN_NESTING, min(state.nesting, bound)))
