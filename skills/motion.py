"""Shared helpers for the skills that move the robot (robot/docs/service.md): they ask the robot service for a
motion primitive with the step's lease token and turn its reply into a SkillOutcome. Skill processes never
touch DDS. Standard library, contract, robot's client surface and skills only."""

from __future__ import annotations

import math
import time
from typing import NamedTuple

from contract import backend
from contract.schema import ErrorCode, describe_exception
from robot import client as robot_client
from robot import faults, protocol
from robot.client import Refused, ServiceUnavailable
from robot.protocol import MoveTarget
from skills import runner

PROCESS_OVERHEAD_S = 5.0  # process start + robot service connect + two state samples (tunable)
MOTION_BASE_S = PROCESS_OVERHEAD_S + protocol.SPORT_CLIENT_TIMEOUT_S  # plus one lost StopMove reply
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
    return faults.inject_fault() if backend.backend_name() == "stub" else None


def _refused(e: Refused | ServiceUnavailable, obs: dict, timing: dict) -> runner.SkillOutcome:
    robot_client.session.drop()
    if isinstance(e, ServiceUnavailable):
        code, message = ErrorCode.ROBOT_UNAVAILABLE, describe_exception(e)
    elif e.code == protocol.ERR_SDK_EXCEPTION:
        code, message = ErrorCode.EXCEPTION, e.message
    else:
        code, message = ErrorCode.MOTION_REJECTED, f"{e.code}: {e.message}"
    return runner.SkillOutcome.error(code, message, observations=obs, timing=timing)


def move(velocity: Velocity, duration_s: float, target: MoveTarget | None = None) -> runner.SkillOutcome:
    """Ask the robot service to command ``velocity`` for ``duration_s`` at its CMD_PERIOD_S, then StopMove; with
    ``target``, the service stops once it is reached and ``duration_s`` is the cap.

    Observations: ``duration_s`` (Moves sent x period), ``sdk_ret`` (the failing Move's code, else the
    StopMove's) and, with a target, ``closed_loop`` (False if the service fell back to open loop) and
    ``turned_deg`` (the measured yaw change at the stop, unsigned degrees; None after a fallback)."""
    fault = _fault()
    if fault == "error":
        obs = {"duration_s": 0.0, "sdk_ret": faults.STUB_ERR_INJECTED}
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"Move returned {faults.STUB_ERR_INJECTED}", observations=obs, timing={}
        )
    extra = {"fault": fault} if fault else {}
    t = time.monotonic()
    try:
        client = robot_client.session.client()
        result = client.move(*velocity, duration_s, token=robot_client.lease_token(), target=target, **extra)
    except (Refused, ServiceUnavailable) as e:
        return _refused(e, {}, {"exec_ms": runner.ms_since(t)})
    timing = {"exec_ms": runner.ms_since(t)}
    turned = {}
    if target is not None:
        yaw = result.yaw_rad
        turned = {
            "closed_loop": yaw is not None,
            "turned_deg": None if yaw is None else round(math.degrees(abs(yaw)), 1),
        }
    if result.move_ret != 0:
        msg = f"Move returned {result.move_ret}"
        if result.stop_ret != 0:
            msg += f"; StopMove returned {result.stop_ret}"
        obs = {"duration_s": result.duration_s, "sdk_ret": result.move_ret, **turned}
        return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, msg, observations=obs, timing=timing)
    obs = {"duration_s": result.duration_s, "sdk_ret": result.stop_ret, **turned}
    if result.stop_ret != 0:
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {result.stop_ret}", observations=obs, timing=timing
        )
    return runner.SkillOutcome.ok(observations=obs, timing=timing)


def single_action(call: str, settle_s: float) -> runner.SkillOutcome:
    """Ask the robot service to run ``call`` (one of protocol.ACTIONS); on success wait ``settle_s`` (via
    backend.sleep) so state_after is sampled after the motion ends."""
    fault = _fault()
    if fault == "error":
        obs = {"sdk_ret": faults.STUB_ERR_INJECTED}
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"{call} returned {faults.STUB_ERR_INJECTED}", observations=obs, timing={}
        )
    extra = {"fault": fault} if fault else {}
    t = time.monotonic()
    try:
        ret = robot_client.session.client().action(call, token=robot_client.lease_token(), **extra)
    except (Refused, ServiceUnavailable) as e:
        return _refused(e, {}, {"exec_ms": runner.ms_since(t)})
    obs = {"sdk_ret": ret}
    if ret != 0:
        timing = {"exec_ms": runner.ms_since(t)}
        return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, f"{call} returned {ret}", observations=obs, timing=timing)
    backend.sleep(settle_s)
    return runner.SkillOutcome.ok(observations=obs, timing={"exec_ms": runner.ms_since(t)})
