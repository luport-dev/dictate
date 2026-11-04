"""Linux-only: the evdev listener needs python-evdev and /dev/input. Only its
pure mapping logic is tested; no device is opened."""
import pytest

evdev = pytest.importorskip("evdev")

import core  # noqa: E402
import evdev_listener as E  # noqa: E402


def test_function_keys_are_mapped():
    from evdev import ecodes
    for i in range(1, 13):
        assert E._KEY_MAP[getattr(ecodes, f"KEY_F{i}")] in core.KEY_MAP[f"f{i}"]


def test_mouse_buttons_match_the_hotkey_sentinels():
    from evdev import ecodes
    assert E._KEY_MAP[ecodes.BTN_BACK] in core.KEY_MAP["mouse_back"]
    assert E._KEY_MAP[ecodes.BTN_TASK] in core.KEY_MAP["mouse_task"]


def test_unmapped_keys_compare_by_code_only():
    assert E._OtherKey(30) == E._OtherKey(30)
    assert E._OtherKey(30) != E._OtherKey(31)
    assert len({E._OtherKey(30), E._OtherKey(30)}) == 1


def test_stop_before_start_is_harmless():
    E.Listener().stop()
