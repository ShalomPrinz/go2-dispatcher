"""Utility: send StopMove and sample state afterwards (docs/safety.md). Not a skill.

``python -m skills.stop_move '{}'``. No orphan watchdog: it must finish even if
the parent died. Stub faults are ignored. StopMove is sent even if the params
argument is invalid (the response then reports invalid_params).
"""

from __future__ import annotations

import time

from skills import backend, result, stub
from skills.schema import ErrorCode

SKILL = "stop_move"
SETTLE_S = 0.5


def body(out: dict) -> None:
    t0 = time.monotonic()
    stub.disable_faults()  # utilities ignore faults
    try:
        result.parse_params()
        params_error = None
    except result.InvalidParams as e:
        params_error = e  # raised only after StopMove was sent
    ret = backend.get_sport_client().StopMove()
    out["timing"]["stop_call_ms"] = result.ms_since(t0)
    out["observations"] = {"sdk_ret": ret}
    backend.sleep(SETTLE_S)
    state_errors: list[str] = []
    out["state_after"], dt = result.sample_state_safe(state_errors, "after")
    out["state_error"] = "; ".join(state_errors) or None
    out["timing"]["state_ms"] = round(dt * 1000.0, 3)
    if params_error is not None:
        raise params_error
    if ret == 0:
        out["status"] = "ok"
    else:
        out.update(error_code=ErrorCode.SDK_ERROR, error_message=f"StopMove returned {ret}")


def main() -> None:
    result.run_main(SKILL, body, watchdog=False)


if __name__ == "__main__":
    main()
