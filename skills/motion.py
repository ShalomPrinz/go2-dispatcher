"""Shared helpers for the skills that call the sport client (skills/docs/skills.md): the
constant-velocity 10 Hz command loop, and single SDK actions with a settle wait.
Standard library and skills only."""

from __future__ import annotations

import sys
import time
import traceback
from typing import NamedTuple

from skills import backend, process, runner
from skills.schema import ErrorCode

CMD_PERIOD_S = 0.1  # Move command period of the move loop (10 Hz)
MOTION_BASE_S = 10.0  # process start + SDK init + state samples (tunable)
MOTION_FACTOR = 1.5  # safety factor on commanded motion time (tunable)


class Velocity(NamedTuple):
    """Body-frame velocity for Move: m/s forward and left, rad/s counter-clockwise."""

    vx: float
    vy: float
    vyaw: float


def motion_timeout(motion_s: float) -> float:
    """Process timeout for a move-loop skill commanding ``motion_s`` seconds of motion."""
    return MOTION_BASE_S + MOTION_FACTOR * motion_s


# --- actions --------------------------------------------------------------------


def _safe_stop(client) -> int | None:
    """StopMove during cleanup after an exception; never raises."""
    try:
        return client.StopMove()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


def move_loop(velocity: Velocity, duration_s: float, *, period_s: float = CMD_PERIOD_S) -> runner.SkillOutcome:
    """Command Move(*velocity) every ``period_s`` for ``duration_s``, then StopMove().

    Returns a SkillOutcome whose observations hold ``duration_s`` (iterations commanded × period), ``sdk_ret`` and
    ``orphaned: true`` when the loop broke because the parent died."""
    t = time.monotonic()
    client = backend.get_sport_client()
    timing = {"init_ms": runner.ms_since(t)}
    t = time.monotonic()
    n = max(1, round(duration_s / period_s))
    obs: dict = {}
    sent = 0
    try:
        for _ in range(n):
            if process.orphaned():
                obs["orphaned"] = True
                break
            ret = client.Move(*velocity)
            if ret != 0:
                stop_ret = client.StopMove()
                msg = f"Move returned {ret}"
                if stop_ret != 0:
                    msg += f"; StopMove returned {stop_ret}"
                obs.update(duration_s=round(sent * period_s, 3), sdk_ret=ret)
                timing["exec_ms"] = runner.ms_since(t)
                return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, msg, observations=obs, timing=timing)
            sent += 1
            backend.sleep(period_s)
    except BaseException:
        _safe_stop(client)
        raise
    ret = client.StopMove()
    timing["exec_ms"] = runner.ms_since(t)
    obs.update(duration_s=round(sent * period_s, 3), sdk_ret=ret)
    if ret != 0:
        return runner.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {ret}", observations=obs, timing=timing
        )
    return runner.SkillOutcome.ok(observations=obs, timing=timing)


def single_action(call: str, settle_s: float) -> runner.SkillOutcome:
    """Call ``client.<call>()``; on success wait ``settle_s`` (via backend.sleep) so
    state_after is sampled after the motion ends. Returns a SkillOutcome."""
    t = time.monotonic()
    client = backend.get_sport_client()
    timing = {"init_ms": runner.ms_since(t)}
    t = time.monotonic()
    ret = getattr(client, call)()
    obs = {"sdk_ret": ret}
    if ret != 0:
        timing["exec_ms"] = runner.ms_since(t)
        return runner.SkillOutcome.error(ErrorCode.SDK_ERROR, f"{call} returned {ret}", observations=obs, timing=timing)
    backend.sleep(settle_s)
    timing["exec_ms"] = runner.ms_since(t)
    return runner.SkillOutcome.ok(observations=obs, timing=timing)
