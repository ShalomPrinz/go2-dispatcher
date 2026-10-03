"""Utility: sample robot state once (skills/docs/robot.md). Not a skill.

``python -m skills.read_state '{}'``. No faults, no watchdog.
"""

from __future__ import annotations

import sys
import traceback

from skills import backend, result, stub
from skills.result import SkillOutcome, StateSampler
from skills.schema import ErrorCode

SKILL = "read_state"


def body(states: StateSampler) -> SkillOutcome:
    stub.disable_faults()  # utilities ignore faults
    result.parse_params()
    backend.backend_name()  # raises BackendNotConfigured
    try:
        states.take("after", strict=True)
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return SkillOutcome.error(ErrorCode.STATE_UNAVAILABLE, result.describe_exception(e), observations={}, timing={})
    return SkillOutcome.ok(observations={}, timing={})


def main() -> None:
    result.run_main(SKILL, body, watchdog=False)


if __name__ == "__main__":
    main()
