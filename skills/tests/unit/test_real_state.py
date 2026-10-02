"""Real-robot state mapping from a SportModeState message (skills/docs/robot.md). Pure code,
importable without the SDK; the posture the model sees in the study comes from it."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from skills import real
from skills.real import POSTURE_STANDING_MIN_M, derive_posture


def msg(**overrides):
    fields = dict(
        mode=1,
        gait_type=0,
        body_height=POSTURE_STANDING_MIN_M,
        position=[1.0, 2.0, 0.3],
        velocity=[0.0, 0.0, 0.0],
        yaw_speed=0.0,
        imu_state=SimpleNamespace(rpy=[0.0, 0.0, 0.1]),
        foot_force=[10, 11, 12, 13],
        error_code=0,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class Item:
    """numpy-like scalar."""

    def __init__(self, v):
        self.v = v

    def item(self):
        return self.v


class Value:
    """ctypes-like scalar."""

    def __init__(self, v):
        self.value = v


def test_full_message_maps():
    state = real._state_from_msg(msg())
    assert (state["backend"], state["posture"]) == ("real", "standing")
    assert state["position"] == [1.0, 2.0, 0.3]
    assert state["imu_rpy"] == [0.0, 0.0, 0.1]
    assert state["foot_force"] == [10.0, 11.0, 12.0, 13.0]
    assert (state["mode"], state["error_code"]) == (1, 0)


def test_missing_attributes_are_none():
    state = real._state_from_msg(SimpleNamespace())
    assert state["posture"] == "unknown"
    assert all(state[k] is None for k in state if k not in ("t", "backend", "posture"))


@pytest.mark.parametrize(
    "field, value",
    [
        ("body_height", math.nan),
        ("position", [1.0, math.nan, 0.3]),
        ("position", [1.0, 2.0]),
        ("foot_force", None),
        ("mode", math.inf),
        ("mode", True),
        ("yaw_speed", "fast"),
    ],
    ids=["nan_height", "nan_in_list", "short_list", "none_list", "inf_int", "bool_int", "text_float"],
)
def test_bad_values_become_none(field, value):
    assert real._state_from_msg(msg(**{field: value}))[field] is None


def test_numpy_and_ctypes_scalars():
    state = real._state_from_msg(
        msg(body_height=Item(POSTURE_STANDING_MIN_M), mode=Value(2), position=[Item(1.0), Value(2.0), 3])
    )
    assert state["body_height"] == POSTURE_STANDING_MIN_M
    assert state["posture"] == "standing"
    assert state["mode"] == 2
    assert state["position"] == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    "height, expected",
    [
        (0.1499, "sitting"),
        (0.15, "unknown"),
        (0.2199, "unknown"),
        (0.22, "standing"),
        (None, "unknown"),
    ],
)
def test_thresholds(height, expected):
    assert derive_posture(height, None) == expected


def test_mode_is_ignored_in_v1():
    assert derive_posture(0.32, 1) == derive_posture(0.32, 7) == "standing"
    assert derive_posture(None, 1) == "unknown"
