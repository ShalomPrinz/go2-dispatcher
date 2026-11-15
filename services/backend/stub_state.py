"""The stub's state file (services/backend/docs/backend.md): posture, an odometry pose, the running velocity command
and an injected robot fault in a shared JSON file. Standard library and contract only."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from contract.env import STUB_STATE_FILE
from contract.faults import clear_fault
from contract.stub import CONDITIONS, DEFAULT_POSTURE, DEFAULT_STATE_FILE, STUB_POSTURES, Condition, StubPosture


@dataclass(frozen=True)
class Pose:
    """Odometry pose in the world frame: metres, yaw in radians counter-clockwise from +x."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


Command = tuple[float, float, float]  # body-frame (vx, vy, vyaw) of the last accepted Move


@dataclass(frozen=True)
class StubState:
    posture: StubPosture = DEFAULT_POSTURE
    pose: Pose = Pose()
    command: Command | None = None  # set by Move, cleared by StopMove: the robot keeps it until then
    sequence_s: float = 0.0  # unscaled time waited since the command was set: the dead time's progress
    condition: Condition | None = None  # an injected robot fault: fallen until reset, stuck until the sequence ends


# --- settings -----------------------------------------------------------------


def state_file() -> Path:
    return Path(os.environ.get(STUB_STATE_FILE) or DEFAULT_STATE_FILE)


# --- state file -----------------------------------------------------------------


def read_state(path: Path | None = None) -> StubState:
    """The state file; a missing file means standing at the origin, a missing pose the origin, a missing
    command or condition none and a missing sequence time zero."""
    path = path or state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return StubState()
    try:
        posture = data["posture"]
        if posture not in STUB_POSTURES:
            raise ValueError(posture)
        pose = data.get("pose")
        command = data.get("command")
        condition = data.get("condition")
        if condition is not None and condition not in CONDITIONS:
            raise ValueError(condition)
        return StubState(
            posture=posture,
            pose=Pose() if pose is None else Pose(**{k: float(pose[k]) for k in ("x", "y", "yaw")}),
            command=None if command is None else (float(command[0]), float(command[1]), float(command[2])),
            sequence_s=float(data.get("sequence_s", 0.0)),
            condition=condition,
        )
    except (TypeError, KeyError, ValueError, AttributeError, IndexError) as e:
        raise ValueError(f"invalid stub state file {path}: {data!r}") from e


def read_posture(path: Path | None = None) -> StubPosture:
    return read_state(path).posture


def write_state(state: StubState, path: Path) -> None:
    """Atomically replace the state file (temp file in the same folder + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_posture(posture: str) -> None:
    if posture not in STUB_POSTURES:
        raise ValueError(f"posture must be one of {STUB_POSTURES}, got {posture!r}")


def write_posture(posture: StubPosture, path: Path | None = None) -> None:
    """Set the posture, keeping the rest."""
    _check_posture(posture)
    path = path or state_file()
    write_state(replace(read_state(path), posture=posture), path)


def write_pose(pose: Pose, path: Path | None = None) -> None:
    """Set the pose, keeping the rest."""
    path = path or state_file()
    write_state(replace(read_state(path), pose=pose), path)


def write_command(command: Command | None, path: Path | None = None) -> None:
    """Set or clear the running command, keeping the rest; a command set from none, or cleared, starts a new
    Move sequence, which ends a stuck condition. A fallen robot keeps no command."""
    path = path or state_file()
    state = read_state(path)
    if state.condition == "fallen":
        return
    if command is not None and state.command is not None:
        write_state(replace(state, command=command), path)
    else:
        write_state(replace(state, command=command, sequence_s=0.0, condition=None), path)


def reset(posture: StubPosture, path: Path | None = None) -> None:
    """Put the stub into a known state: ``posture`` at the origin, no command, no pending robot fault; the
    dispatcher's only write to stub state."""
    _check_posture(posture)
    path = path or state_file()
    write_state(StubState(posture=posture), path)
    clear_fault(path)
