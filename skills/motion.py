"""Shared helpers for the skills that call the sport client (skills/docs/skills.md): parameter checks,
the constant-velocity 10 Hz command loop, and single SDK actions with a settle wait.
Standard library and skills only."""

from __future__ import annotations

import math
import sys
import time
import traceback

from skills import backend, result

# --- parameter checks (presence, type, enum membership; ranges are the dispatcher's) ---


def require_enum(params: dict, key: str, values: tuple[str, ...]) -> str:
    value = params.get(key)
    if key not in params:
        raise result.InvalidParams(f"missing required param '{key}'")
    if not isinstance(value, str) or value not in values:
        raise result.InvalidParams(f"'{key}' must be one of {', '.join(values)}, got {value!r}")
    return value


def require_number(params: dict, key: str) -> float:
    if key not in params:
        raise result.InvalidParams(f"missing required param '{key}'")
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise result.InvalidParams(f"'{key}' must be a finite number, got {value!r}")
    return float(value)


# --- actions --------------------------------------------------------------------


def _safe_stop(client) -> int | None:
    """StopMove during cleanup after an exception; never raises."""
    try:
        return client.StopMove()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


def move_loop(vx: float, vy: float, vyaw: float, duration_s: float, period_s: float):
    """Command Move(vx, vy, vyaw) every ``period_s`` for ``duration_s``, then StopMove().

    Returns (status, observations, error_code, error_message, timing). Observations
    hold ``duration_s`` (iterations commanded × period), ``sdk_ret`` and
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
                return "error", obs, "sdk_error", msg, timing
            sent += 1
            backend.sleep(period_s)
    except BaseException:
        _safe_stop(client)
        raise
    ret = client.StopMove()
    timing["exec_ms"] = result.ms_since(t)
    obs.update(duration_s=round(sent * period_s, 3), sdk_ret=ret)
    if ret != 0:
        return "error", obs, "sdk_error", f"StopMove returned {ret}", timing
    return "ok", obs, None, None, timing


def single_action(call: str, settle_s: float):
    """Call ``client.<call>()``; on success wait ``settle_s`` (via backend.sleep) so
    state_after is sampled after the motion ends. Returns the Body tuple."""
    t = time.monotonic()
    client = backend.get_sport_client()
    timing = {"init_ms": result.ms_since(t)}
    t = time.monotonic()
    ret = getattr(client, call)()
    obs = {"sdk_ret": ret}
    if ret != 0:
        timing["exec_ms"] = result.ms_since(t)
        return "error", obs, "sdk_error", f"{call} returned {ret}", timing
    backend.sleep(settle_s)
    timing["exec_ms"] = result.ms_since(t)
    return "ok", obs, None, None, timing
