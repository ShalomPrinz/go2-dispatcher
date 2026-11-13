"""Step verdicts from sampled state: fault detection and goal verification (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import math
from typing import Any, NamedTuple

from contract.constants import YAW_SIGN
from contract.manifest import Expectation, YawDelta
from contract.schema import ErrorCode

from .models import Fault, Posture, RobotState, SkillResponse, Verdict

# Fault thresholds: assumed signatures, unverified on the robot (tunable) (dispatcher/docs/loop-and-context.md)
FALLEN_TILT_RAD = 0.5  # |roll| or |pitch| above this (about 29 deg) is a fall; standing stays within 0.14 rad
FAULT_MODES = frozenset({7})  # sport mode 7, damping: the robot is not holding itself up
# an SDK call returned non-zero; ErrorCode is a str, so the response's plain code matches (contract/docs/contract.md)
REJECTED_ERROR_CODES = frozenset({ErrorCode.SDK_ERROR})

__all__ = ["Verification", "detect_faults", "check_goal", "verify"]


class Verification(NamedTuple):
    verdict: Verdict | None  # None: the step's outcome was not ok, so no goal check ran
    faults: list[Fault]


def detect_faults(response: SkillResponse | None) -> list[Fault] | None:
    """Universal faults in the step's response; None when ``state_after`` is missing and none was found."""
    faults: list[Fault] = []
    if response is not None and response.error is not None and response.error.code in REJECTED_ERROR_CODES:
        faults.append("command_rejected")
    after = response.state_after if response is not None else None
    if after is not None:
        if after.imu_rpy is not None and max(abs(after.imu_rpy[0]), abs(after.imu_rpy[1])) > FALLEN_TILT_RAD:
            faults.append("fallen")
        if after.mode is not None and after.mode in FAULT_MODES:
            faults.append("fault_mode")
    elif not faults:
        return None
    return faults


def _posture(expected: Posture, after: RobotState) -> Verdict:
    if after.posture == "unknown" and after.body_height is None:
        return "unavailable"
    return "verified" if after.posture == expected else "failed"


def _yaw(yd: YawDelta, params: dict[str, Any], before: RobotState | None, after: RobotState) -> Verdict:
    if before is None or before.imu_rpy is None or after.imu_rpy is None:
        return "unavailable"
    delta = math.degrees(after.imu_rpy[2] - before.imu_rpy[2])
    expected = YAW_SIGN[params[yd.direction_param]] * params[yd.param]
    return "verified" if abs(math.remainder(delta - expected, 360.0)) <= yd.tolerance_deg else "failed"


def check_goal(expect: Expectation | None, params: dict[str, Any], response: SkillResponse | None) -> Verdict:
    """Whether the step reached its declared goal (contract/docs/contract.md); every given key must hold."""
    if expect is None:
        return "unverified"
    if response is None:
        return "unavailable"
    if expect.self_reported:
        return "verified" if response.status == "ok" else "failed"
    after = response.state_after
    if after is None:
        return "unavailable"
    checks: list[Verdict] = []
    if expect.posture is not None:
        checks.append(_posture(expect.posture, after))
    if expect.mode is not None:
        checks.append("unavailable" if after.mode is None else "verified" if after.mode == expect.mode else "failed")
    if expect.yaw_delta is not None:
        checks.append(_yaw(expect.yaw_delta, params, response.state_before, after))
    if "failed" in checks:
        return "failed"
    return "unavailable" if "unavailable" in checks else "verified"


def verify(
    expect: Expectation | None, params: dict[str, Any], response: SkillResponse | None, *, ok: bool
) -> Verification:
    """One verdict for a step with outcome ``ok``: a detected fault forces ``failed``; without ``state_after``
    nothing is ``verified``. Any other outcome gets fault detection only and no verdict."""
    faults = detect_faults(response)
    if not ok:
        return Verification(None, faults or [])
    if faults:
        return Verification("failed", faults)
    goal = check_goal(expect, params, response)
    if faults is None and goal != "failed":  # fault detection could not run
        return Verification("unavailable", [])
    return Verification(goal, [])
