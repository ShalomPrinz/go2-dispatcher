"""Shared helpers for the skills that move the robot (skills/docs/skills.md): they ask the robot service for a
motion primitive with the step's lease token and turn its reply into a SkillOutcome. Skill processes never
touch DDS. Standard library and skills only."""

from __future__ import annotations

import time
from typing import NamedTuple

from skills import backend, robot_ipc, runner, stub
from skills.robot_ipc import Refused, ServiceUnavailable
from skills.schema import ErrorCode

PROCESS_OVERHEAD_S = 5.0  # process start + robot service connect + two state samples (tunable)
MOTION_BASE_S = PROCESS_OVERHEAD_S + robot_ipc.SPORT_CLIENT_TIMEOUT_S  # plus one lost StopMove reply
MOTION_FACTOR = 1.5  # safety factor on commanded motion time (tunable)


class Velocity(NamedTuple):
    """Body-frame velocity for Move: m/s forward and left, rad/s counter-clockwise."""

    vx: float
    vy: float
    vyaw: float


def motion_timeout(motion_s: float) -> float:
    """Process timeout for a skill commanding ``motion_s`` seconds of motion."""
    return MOTION_BASE_S + MOTION_FACTOR * motion_s


def _fault() -> str | None:
    """The stub fault for this skill's first primitive: "error", a service fault to forward, or None."""
    return stub.inject_fault() if backend.backend_name() == "stub" else None


def _refused(e: Refused | ServiceUnavailable, obs: dict, timing: dict) -> runner.SkillOutcome:
    robot_ipc.session.drop()
    if isinstance(e, ServiceUnavailable):
        code, message = ErrorCode.ROBOT_UNAVAILABLE, runner.describe_exception(e)
    elif e.code == robot_ipc.ERR_SDK_EXCEPTION:
        code, message = ErrorCode.EXCEPTION, e.message
    else:
        code, message = ErrorCode.MOTION_REJECTED, f"{e.code}: {e.message}"
    return runner.SkillOutcome.error(code, message, observations=obs, timing=timing)


def move(velocity: Velocity, duration_s: float) -> runner.SkillOutcome:
    """Ask the robot service to command ``velocity`` for ``duration_s`` at its CMD_PERIOD_S, then StopMove.

    Observations: ``duration_s`` (Moves sent x period) and ``sdk_ret`` (the failing Move's code, else the
    StopMove's)."""
    fault = _fault()
    if fault == "error":
        obs = {"duration_s": 0.0, "sdk_ret": stub.STUB_ERR_INJECTED}
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"Move returned {stub.STUB_ERR_INJECTED}", observations=obs, timing={}
        )
    extra = {"fault": fault} if fault else {}
    t = time.monotonic()
    try:
        client = robot_ipc.session.client()
        result = client.move(*velocity, duration_s, token=robot_ipc.lease_token(), **extra)
    except (Refused, ServiceUnavailable) as e:
        return _refused(e, {}, {"exec_ms": runner.ms_since(t)})
    timing = {"exec_ms": runner.ms_since(t)}
    if result.move_ret != 0:
        msg = f"Move returned {result.move_ret}"
        if result.stop_ret != 0:
            msg += f"; StopMove returned {result.stop_ret}"
        obs = {"duration_s": result.duration_s, "sdk_ret": result.move_ret}
        return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, msg, observations=obs, timing=timing)
    obs = {"duration_s": result.duration_s, "sdk_ret": result.stop_ret}
    if result.stop_ret != 0:
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {result.stop_ret}", observations=obs, timing=timing
        )
    return runner.SkillOutcome.ok(observations=obs, timing=timing)


def single_action(call: str, settle_s: float) -> runner.SkillOutcome:
    """Ask the robot service to run ``call`` (one of robot_ipc.ACTIONS); on success wait ``settle_s`` (via
    backend.sleep) so state_after is sampled after the motion ends."""
    fault = _fault()
    if fault == "error":
        obs = {"sdk_ret": stub.STUB_ERR_INJECTED}
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"{call} returned {stub.STUB_ERR_INJECTED}", observations=obs, timing={}
        )
    extra = {"fault": fault} if fault else {}
    t = time.monotonic()
    try:
        ret = robot_ipc.session.client().action(call, token=robot_ipc.lease_token(), **extra)
    except (Refused, ServiceUnavailable) as e:
        return _refused(e, {}, {"exec_ms": runner.ms_since(t)})
    obs = {"sdk_ret": ret}
    if ret != 0:
        timing = {"exec_ms": runner.ms_since(t)}
        return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, f"{call} returned {ret}", observations=obs, timing=timing)
    backend.sleep(settle_s)
    return runner.SkillOutcome.ok(observations=obs, timing={"exec_ms": runner.ms_since(t)})
