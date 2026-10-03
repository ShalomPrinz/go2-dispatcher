"""Shared helpers for the skills that call the sport client (skills/docs/skills.md): the
constant-velocity 10 Hz command loop, and single SDK actions with a settle wait.
Standard library and skills only."""

from __future__ import annotations

import sys
import time
import traceback

from skills import backend, result
from skills.schema import ErrorCode

# --- actions --------------------------------------------------------------------


def _safe_stop(client) -> int | None:
    """StopMove during cleanup after an exception; never raises."""
    try:
        return client.StopMove()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


def move_loop(vx: float, vy: float, vyaw: float, duration_s: float, period_s: float) -> result.SkillOutcome:
    """Command Move(vx, vy, vyaw) every ``period_s`` for ``duration_s``, then StopMove().

    Returns a SkillOutcome whose observations hold ``duration_s`` (iterations commanded × period), ``sdk_ret`` and
    ``orphaned: true`` when the loop broke because the parent died."""
    t = time.monotonic()
    client = backend.get_sport_client()
    timing = {"init_ms": result.ms_since(t)}
    t = time.monotonic()
    n = max(1, round(duration_s / period_s))
    obs: dict = {}
    sent = 0
    try:
        for _ in range(n):
            if result.orphaned():
                obs["orphaned"] = True
                break
            ret = client.Move(vx, vy, vyaw)
            if ret != 0:
                stop_ret = client.StopMove()
                msg = f"Move returned {ret}"
                if stop_ret != 0:
                    msg += f"; StopMove returned {stop_ret}"
                obs.update(duration_s=round(sent * period_s, 3), sdk_ret=ret)
                timing["exec_ms"] = result.ms_since(t)
                return result.SkillOutcome.error(ErrorCode.SDK_ERROR, msg, observations=obs, timing=timing)
            sent += 1
            backend.sleep(period_s)
    except BaseException:
        _safe_stop(client)
        raise
    ret = client.StopMove()
    timing["exec_ms"] = result.ms_since(t)
    obs.update(duration_s=round(sent * period_s, 3), sdk_ret=ret)
    if ret != 0:
        return result.SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {ret}", observations=obs, timing=timing
        )
    return result.SkillOutcome.ok(observations=obs, timing=timing)


def single_action(call: str, settle_s: float) -> result.SkillOutcome:
    """Call ``client.<call>()``; on success wait ``settle_s`` (via backend.sleep) so
    state_after is sampled after the motion ends. Returns a SkillOutcome."""
    t = time.monotonic()
    client = backend.get_sport_client()
    timing = {"init_ms": result.ms_since(t)}
    t = time.monotonic()
    ret = getattr(client, call)()
    obs = {"sdk_ret": ret}
    if ret != 0:
        timing["exec_ms"] = result.ms_since(t)
        return result.SkillOutcome.error(ErrorCode.SDK_ERROR, f"{call} returned {ret}", observations=obs, timing=timing)
    backend.sleep(settle_s)
    timing["exec_ms"] = result.ms_since(t)
    return result.SkillOutcome.ok(observations=obs, timing=timing)
