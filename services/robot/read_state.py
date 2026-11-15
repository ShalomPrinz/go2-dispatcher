"""Utility: sample robot state once, with its own DDS on the real backend, not through the robot service
(services/robot/docs/robot.md). Not a skill.

``python -m services.robot.read_state '{}'``. Stub faults do not apply; no watchdog.
"""

from __future__ import annotations

import sys
import traceback

from contract import backend, policy, runtime
from contract.runtime import SkillOutcome, StateSampler
from contract.schema import ErrorCode, describe_exception
from services.robot import local

TIMEOUT_S = 10.0

POLICY = policy.SkillPolicy(name="read_state", timeout=TIMEOUT_S)  # utility: zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    runtime.parse_params()
    backend.backend_name()  # raises BackendNotConfigured
    try:
        states.take("after", strict=True)
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return SkillOutcome.error(ErrorCode.STATE_UNAVAILABLE, describe_exception(e), observations={}, timing={})
    return SkillOutcome.ok(observations={}, timing={})


def main() -> None:
    states = StateSampler(reader=local.sample_state, prepare=local.init_dds)
    runtime.run_main(POLICY.name, body, watchdog=False, states=states, setup=local.setup_timing)


if __name__ == "__main__":
    main()
