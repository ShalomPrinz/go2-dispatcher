"""derive_posture thresholds and None handling (docs/robot.md)."""

from __future__ import annotations

import pytest

from go2_skills.posture import derive_posture


@pytest.mark.parametrize("height, expected", [
    (0.1499, "sitting"),
    (0.15, "unknown"),
    (0.2199, "unknown"),
    (0.22, "standing"),
    (None, "unknown"),
])
def test_thresholds(height, expected):
    assert derive_posture(height, None) == expected


def test_mode_is_ignored_in_v1():
    assert derive_posture(0.32, 1) == derive_posture(0.32, 7) == "standing"
    assert derive_posture(None, 1) == "unknown"

