"""Utility: send StopMove and sample state afterwards (docs/safety.md). Not a skill.

``python -m skills.stop_move '{}'``. No orphan watchdog: it must finish even if
the parent died. Stub faults are ignored. StopMove is sent even if the params
argument is invalid (the response then reports invalid_params).
"""

from __future__ import annotations

import os
import sys
import time
import traceback

from skills import backend, result

SKILL = "stop_move"
SETTLE_S = 0.5


def main() -> None:
    result.capture_stdout()
    t0 = time.monotonic()
    os.environ.pop("GO2_STUB_FAULT", None)  # utilities ignore faults
    timing: dict = {}
    obs: dict = {}
    state_after = None
    state_errors: list[str] = []
    try:
        params_error = None
        try:
            result.parse_params()
        except result.InvalidParams as e:
            params_error = str(e)
        client = backend.get_sport_client()
        ret = client.StopMove()
        timing["stop_call_ms"] = result.ms_since(t0)
        obs["sdk_ret"] = ret
        backend.sleep(SETTLE_S)
        state_after, dt = result.sample_state_safe(state_errors, "after")
        timing["state_ms"] = round(dt * 1000.0, 3)
        if params_error is not None:
            status, code, msg = "error", "invalid_params", params_error
        elif ret == 0:
            status, code, msg = "ok", None, None
        else:
            status, code, msg = "error", "sdk_error", f"StopMove returned {ret}"
    except Exception as e:
        if not isinstance(e, backend.BackendNotConfigured):
            traceback.print_exc(file=sys.stderr)
        code, msg = result.error_from_exception(e)
        status = "error"
    timing["total_ms"] = result.ms_since(t0)
    result.emit(SKILL, status, observations=obs, error_code=code, error_message=msg,
                state_after=state_after, state_error="; ".join(state_errors) or None,
                timing=timing)


if __name__ == "__main__":
    main()
