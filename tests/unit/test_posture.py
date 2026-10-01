"""derive_posture thresholds and None handling (§9, OD-2)."""

from __future__ import annotations

import pytest

from go2_skills import posture
from go2_skills.posture import derive_posture


@pytest.mark.parametrize("height, expected", [
    (0.0, "sitting"),
    (0.08, "sitting"),
    (0.1499, "sitting"),
    (0.15, "unknown"),
    (0.20, "unknown"),
    (0.2199, "unknown"),
    (0.22, "standing"),
    (0.32, "standing"),
    (None, "unknown"),
])
def test_thresholds(height, expected):
    assert derive_posture(height, None) == expected


def test_mode_is_ignored_in_v1():
    assert derive_posture(0.32, 1) == derive_posture(0.32, 7) == "standing"
    assert derive_posture(None, 1) == "unknown"


def test_thresholds_are_named_constants(monkeypatch):
    assert posture.POSTURE_SITTING_MAX_M == 0.15
    assert posture.POSTURE_STANDING_MIN_M == 0.22
    monkeypatch.setattr(posture, "POSTURE_STANDING_MIN_M", 0.30)
    assert derive_posture(0.25, None) == "unknown"
