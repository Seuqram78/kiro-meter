"""Pure keyboard-driven UI state for the live view (not persisted)."""

from __future__ import annotations

from dataclasses import dataclass, replace

import readchar

from kiro_meter.db import FULL_NESTING

_MIN_NESTING = 1
_SCROLL_DELTA = {readchar.key.UP: -1, readchar.key.DOWN: 1}
# left = collapse (less detail), right = expand (more detail)
_NESTING_DELTA = {readchar.key.LEFT: -1, readchar.key.RIGHT: 1}


@dataclass(frozen=True)
class LiveState:
    """Scroll position, folder nesting, and local-visibility for one run.

    Reset to defaults every launch - this is deliberately not persisted to
    ``~/.kiro-meter/config.toml``.
    """

    nesting: int = FULL_NESTING
    scroll: int = 0
    show_local: bool = True
    quit: bool = False


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
