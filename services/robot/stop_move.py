"""Utility: the fallback stop. Send StopMove from this fresh process, with its own DDS and sport client, not
through the robot service, and sample state afterwards (docs/safety.md). Not a skill.

``python -m services.robot.stop_move '{}'``. No orphan watchdog: it must finish even if
the parent died. Stub faults do not apply. StopMove is sent even if the params
argument is invalid (the response then reports invalid_params).
"""

from __future__ import annotations

import time

from contract import runtime
from contract.runtime import SkillOutcome, StateSampler
from contract.schema import ErrorCode
from services.robot import backends, protocol

SETTLE_S = 0.5

POLICY = protocol.STOP_MOVE.policy  # zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    try:
        runtime.parse_params()
        params_error = None
    except runtime.InvalidParams as e:
        params_error = str(e)  # reported only after StopMove was sent
    robot = backends.load()
    robot.init_dds()  # reported as dds_init_ms
    t = time.monotonic()
    client = robot.sport_client()
    timing = {"client_init_ms": runtime.ms_since(t)}
    t = time.monotonic()
    ret = client.StopMove()
    timing["stop_call_ms"] = runtime.ms_since(t)
    observations = {"sdk_ret": ret}
    robot.sleep(SETTLE_S)
    states.take("after")
    if params_error is not None:
        return SkillOutcome.error(ErrorCode.INVALID_PARAMS, params_error, observations=observations, timing=timing)
    if ret != 0:
        return SkillOutcome.error(
            ErrorCode.SDK_ERROR, f"StopMove returned {ret}", observations=observations, timing=timing
        )
    return SkillOutcome.ok(observations=observations, timing=timing)


def main() -> None:
    states = StateSampler(reader=lambda: backends.load().sample_state(), prepare=lambda: backends.load().init_dds())
    runtime.run_main(POLICY.name, body, watchdog=False, states=states, setup=backends.setup_timing)


if __name__ == "__main__":
    main()
