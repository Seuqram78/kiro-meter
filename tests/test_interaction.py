"""Tests for the pure keyboard-driven live-view state reducer."""

from __future__ import annotations

import readchar

from kiro_meter.db import FULL_NESTING
from kiro_meter.interaction import LiveState, apply_key, normalize

_STARTING_NESTING = 3
_STARTING_SCROLL = 5
_MAX_NESTING = 6


def test_up_decreases_scroll() -> None:
    """UP scrolls up (toward the top)."""
    state = apply_key(LiveState(scroll=_STARTING_SCROLL), readchar.key.UP)
    assert state.scroll == _STARTING_SCROLL - 1


def test_up_floors_scroll_at_zero() -> None:
    """UP never takes scroll below 0."""
    state = apply_key(LiveState(scroll=0), readchar.key.UP)
    assert state.scroll == 0


def test_down_increases_scroll() -> None:
    """DOWN scrolls down."""
    state = apply_key(LiveState(scroll=0), readchar.key.DOWN)
    assert state.scroll == 1


def test_left_decreases_nesting() -> None:
    """LEFT collapses the folder grouping further."""
    state = apply_key(LiveState(nesting=_STARTING_NESTING), readchar.key.LEFT)
    assert state.nesting == _STARTING_NESTING - 1


def test_left_floors_nesting_at_one() -> None:
    """LEFT never takes nesting below 1 (a folder always shows something)."""
    state = apply_key(LiveState(nesting=1), readchar.key.LEFT)
    assert state.nesting == 1


def test_right_increases_nesting() -> None:
    """RIGHT shows more path detail."""
    state = apply_key(LiveState(nesting=_STARTING_NESTING), readchar.key.RIGHT)
    assert state.nesting == _STARTING_NESTING + 1


def test_application_mode_arrows_also_scroll_and_nest() -> None:
    """SS3-form arrows (DECCKM/application cursor keys mode) work too."""
    up = apply_key(LiveState(scroll=_STARTING_SCROLL), "\x1bOA")
    assert up.scroll == _STARTING_SCROLL - 1
    down = apply_key(LiveState(scroll=0), "\x1bOB")
    assert down.scroll == 1
    left = apply_key(LiveState(nesting=_STARTING_NESTING), "\x1bOD")
    assert left.nesting == _STARTING_NESTING - 1
    right = apply_key(LiveState(nesting=_STARTING_NESTING), "\x1bOC")
    assert right.nesting == _STARTING_NESTING + 1


def test_l_toggles_show_local() -> None:
    """The 'l' key hides, then re-shows, local-derived sections."""
    hidden = apply_key(LiveState(show_local=True), "l")
    assert hidden.show_local is False
    shown = apply_key(hidden, "l")
    assert shown.show_local is True


def test_q_quits() -> None:
    """'q' requests a clean quit."""
    assert apply_key(LiveState(), "q").quit is True


def test_ctrl_c_quits() -> None:
    """The Ctrl-C sentinel also requests a clean quit."""
    assert apply_key(LiveState(), readchar.key.CTRL_C).quit is True


def test_unrecognized_key_is_a_no_op() -> None:
    """An unmapped key leaves the state unchanged."""
    state = LiveState(nesting=2, scroll=3, show_local=False)
    assert apply_key(state, "x") == state


def test_normalize_clamps_nesting_down_to_data() -> None:
    """A nesting level beyond the real data is clamped down."""
    state = normalize(LiveState(nesting=FULL_NESTING), max_nesting=_MAX_NESTING)
    assert state.nesting == _MAX_NESTING


def test_normalize_leaves_in_bounds_nesting_unchanged() -> None:
    """A Left-press's progress isn't undone by the next tick's normalize."""
    state = normalize(LiveState(nesting=_STARTING_NESTING), max_nesting=_MAX_NESTING)
    assert state.nesting == _STARTING_NESTING


def test_normalize_with_no_data_still_yields_at_least_one() -> None:
    """An empty data set (max_nesting=0) still leaves nesting >= 1."""
    state = normalize(LiveState(nesting=FULL_NESTING), max_nesting=0)
    assert state.nesting == 1


def test_s_cycles_through_sort_states() -> None:
    """'s' advances cr desc -> cr asc -> folder asc -> folder desc -> cr desc."""
    state = LiveState()
    assert state.sort == "cr_desc"
    state = apply_key(state, "s")
    assert state.sort == "cr_asc"
    state = apply_key(state, "s")
    assert state.sort == "folder_asc"
    state = apply_key(state, "s")
    assert state.sort == "folder_desc"
    state = apply_key(state, "s")
    assert state.sort == "cr_desc"
