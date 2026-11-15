"""The stub's fault kinds, shared by the dispatcher (which injects them), the skills and the robot service, and the
sim-only fault file that carries a robot fault to the next arm (contract/docs/contract.md). Standard library only."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal, get_args

from contract.stub import Condition

SkillFault = Literal["error", "hang", "crash", "garbage"]  # applied in the skill process
RobotFault = Literal["service_hang", "service_crash", "stop_error", "rejected", Condition]  # applied by the robot side
FaultKind = Literal[SkillFault, RobotFault]
SKILL_FAULTS: tuple[SkillFault, ...] = get_args(SkillFault)
ROBOT_FAULTS: tuple[RobotFault, ...] = get_args(RobotFault)
FAULT_KINDS: tuple[FaultKind, ...] = get_args(FaultKind)

STUB_ERR_INJECTED = 99  # the SDK return code of an injected error or stop_error
CRASH_EXIT_CODE = 139  # the exit code of a crashed skill or robot service


class BadFaultFile(ValueError):
    """The stub fault file holds bad JSON or no robot fault; the robot service refuses the arm with bad_request."""


def fault_file(state_file: Path) -> Path:
    """The sim-only fault file beside the stub state file."""
    return state_file.with_suffix(".fault.json")


def write_fault(state_file: Path, kind: RobotFault) -> None:
    """Write the robot fault for the next arm, atomically (temp file in the same dir + os.replace)."""
    if kind not in ROBOT_FAULTS:
        raise ValueError(f"{kind!r} is not a robot fault (expected one of {', '.join(ROBOT_FAULTS)})")
    path = fault_file(state_file)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"kind": kind}, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def take_fault(state_file: Path) -> RobotFault | None:
    """Consume the pending robot fault: None if there is none; raises BadFaultFile (the file is removed either way)."""
    path = fault_file(state_file)
    taken = path.with_name(f"{path.name}.{os.getpid()}.taken")
    try:
        os.replace(path, taken)  # claim it first, so a fault written after this read is never lost
    except FileNotFoundError:
        return None
    try:
        raw = taken.read_text(encoding="utf-8")
    finally:
        taken.unlink(missing_ok=True)
    try:
        kind = json.loads(raw)["kind"]
    except (ValueError, TypeError, KeyError) as e:
        raise BadFaultFile(f'{path}: expected {{"kind": <robot fault>}}, got {raw[:80]!r}') from e
    if kind not in ROBOT_FAULTS:
        raise BadFaultFile(f"{path}: {kind!r} is not a robot fault")
    return kind


def clear_fault(state_file: Path) -> None:
    """Drop any pending robot fault (service start, stub reset)."""
    fault_file(state_file).unlink(missing_ok=True)
