"""Utility: sample robot state once (skills/docs/robot.md). Not a skill.

``python -m skills.read_state '{}'``. No faults, no watchdog.
"""

from __future__ import annotations

import sys
import traceback

from skills import backend, policy, runner, stub
from skills.runner import SkillOutcome, StateSampler
from skills.schema import ErrorCode

TIMEOUT_S = 10.0

POLICY = policy.SkillPolicy(name="read_state", timeout=TIMEOUT_S)  # utility: zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    stub.disable_faults()  # utilities ignore faults
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
    runner.run_main(POLICY.name, body, watchdog=False)


if __name__ == "__main__":
    main()
