"""Utility: sample robot state once, with its own DDS on the real backend, not through the robot service
(skills/docs/robot.md). Not a skill.

``python -m skills.read_state '{}'``. Stub faults do not apply; no watchdog.
"""

from __future__ import annotations

import sys
import traceback

from contract import policy
from contract.schema import ErrorCode
from skills import backend, local, runner
from skills.runner import SkillOutcome, StateSampler

TIMEOUT_S = 10.0

POLICY = policy.SkillPolicy(name="read_state", timeout=TIMEOUT_S)  # utility: zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    runner.parse_params()
    backend.backend_name()  # raises BackendNotConfigured
    try:
        states.take("after", strict=True)
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return SkillOutcome.error(ErrorCode.STATE_UNAVAILABLE, runner.describe_exception(e), observations={}, timing={})
    return SkillOutcome.ok(observations={}, timing={})


def main() -> None:
    states = StateSampler(reader=local.sample_state, prepare=local.init_dds)
    runner.run_main(POLICY.name, body, watchdog=False, states=states, setup=local.setup_timing)


if __name__ == "__main__":
    main()
