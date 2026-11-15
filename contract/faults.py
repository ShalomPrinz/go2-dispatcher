"""The stub's fault kinds, shared by the dispatcher (which injects them), the skills and the robot service
(contract/docs/contract.md). Standard library only."""

from __future__ import annotations

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
