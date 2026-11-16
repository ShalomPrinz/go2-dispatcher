"""The stub state file (services/backend/docs/backend.md)."""

from __future__ import annotations

import json

import pytest

from contract.env import STUB_STATE_FILE
from services.backend import stub_state


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A state file in tmp_path, also the default path."""
    path = tmp_path / "logs" / "stub_state.json"
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    return path


def test_reset_overwrites_posture_and_pose_and_rejects_unknown(state):
    stub_state.write_pose(stub_state.Pose(1.0, 2.0, 0.5), state)
    stub_state.reset("sitting", state)
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    with pytest.raises(ValueError):
        stub_state.reset("lying", state)  # pyright: ignore[reportArgumentType]
    assert stub_state.read_posture(state) == "sitting"


def test_writers_merge_and_missing_pose_is_origin(state):
    assert stub_state.read_state(state) == stub_state.StubState(
        "standing", stub_state.Pose()
    )  # no file: standing at the origin
    stub_state.write_posture("sitting", state)  # creates the missing parent directory
    state.write_text(json.dumps({"posture": "sitting"}))
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    stub_state.write_pose(stub_state.Pose(1.0, -2.0, 0.25), state)
    stub_state.write_posture("standing", state)
    stub_state.write_command((0.1, 0.0, 0.0), state)
    pose = {"x": 1.0, "y": -2.0, "yaw": 0.25}
    expected = {"posture": "standing", "pose": pose, "command": [0.1, 0.0, 0.0], "sequence_s": 0.0, "condition": None}
    assert json.loads(state.read_text()) == expected
    state.write_text(json.dumps({"posture": "standing", "pose": {"x": 1.0}}))
    with pytest.raises(ValueError, match="invalid stub state file"):
        stub_state.read_state(state)
