"""Utility: the fallback stop. Send StopMove from this fresh process, with its own DDS and sport client, not
through the robot service, and sample state afterwards (docs/safety.md). Not a skill.

``python -m skills.stop_move '{}'``. No orphan watchdog: it must finish even if
the parent died. Stub faults do not apply. StopMove is sent even if the params
argument is invalid (the response then reports invalid_params).
"""

from __future__ import annotations

import time

from skills import backend, local, policy, runner
from skills.runner import SkillOutcome, StateSampler
from skills.schema import ErrorCode

TIMEOUT_S = 12.0  # covers one lost StopMove reply (tunable, skills/docs/skills.md)
SETTLE_S = 0.5

POLICY = policy.SkillPolicy(name="stop_move", timeout=TIMEOUT_S)  # utility: zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    try:
        runner.parse_params()
        params_error = None
    except runner.InvalidParams as e:
        params_error = str(e)  # reported only after StopMove was sent
    local.init_dds()  # reported as dds_init_ms
    t = time.monotonic()
    client = local.sport_client()
    timing = {"client_init_ms": runner.ms_since(t)}
    t = time.monotonic()
    ret = client.StopMove()
    timing["stop_call_ms"] = runner.ms_since(t)
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
    states = StateSampler(reader=local.sample_state, prepare=local.init_dds)
    runner.run_main(POLICY.name, body, watchdog=False, states=states, setup=local.setup_timing)


if __name__ == "__main__":
    main()
