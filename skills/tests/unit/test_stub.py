"""Stub state reset, the dispatcher's entry point to stub state (skills/docs/skills.md)."""

from __future__ import annotations

import pytest

from skills import stub


def test_reset_overwrites_posture_and_rejects_unknown(tmp_path):
    path = tmp_path / "runs" / "stub_state.json"
    stub.reset("sitting", path)
    assert stub.read_posture(path) == "sitting"
    stub.reset("standing", path)
    assert stub.read_posture(path) == "standing"
    with pytest.raises(ValueError):
        stub.reset("lying", path)  # pyright: ignore[reportArgumentType]
    assert stub.read_posture(path) == "standing"
