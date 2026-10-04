"""Utility: send StopMove and sample state afterwards (docs/safety.md). Not a skill.

``python -m skills.stop_move '{}'``. No orphan watchdog: it must finish even if
the parent died. Stub faults are ignored. StopMove is sent even if the params
argument is invalid (the response then reports invalid_params).
"""

from __future__ import annotations

import time

from skills import backend, policy, runner, stub
from skills.runner import SkillOutcome, StateSampler
from skills.schema import ErrorCode

TIMEOUT_S = 10.0
SETTLE_S = 0.5

POLICY = policy.SkillPolicy(name="stop_move", timeout=TIMEOUT_S)  # utility: zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    t0 = time.monotonic()
    stub.disable_faults()  # utilities ignore faults
    try:
        runner.parse_params()
        params_error = None
    except runner.InvalidParams as e:
        params_error = str(e)  # reported only after StopMove was sent
    ret = backend.get_sport_client().StopMove()
    timing = {"stop_call_ms": runner.ms_since(t0)}
    observations = {"sdk_ret": ret}
    backend.sleep(SETTLE_S)
    states.take("after")
    if params_error is not None:
        return SkillOutcome.error(ErrorCode.INVALID_PARAMS, params_error, observations=observations, timing=timing)
    if ret != 0:
        return SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {ret}", observations=observations, timing=timing
        )
    return SkillOutcome.ok(observations=observations, timing=timing)


def main() -> None:
    runner.run_main(POLICY.name, body, watchdog=False)


if __name__ == "__main__":
    main()
