"""Utility: sample robot state once, with its own DDS on the real backend, not through the robot service
(services/robot/docs/robot.md). Not a skill.

``python -m services.robot.read_state '{}'``. Stub faults do not apply; no watchdog.
"""

from __future__ import annotations

import sys
import traceback

from contract import backend, runtime
from contract.runtime import SkillOutcome, StateSampler
from contract.schema import ErrorCode, describe_exception
from services.robot import backends, protocol

POLICY = protocol.READ_STATE.policy  # zero cost, no observations


def body(states: StateSampler) -> SkillOutcome:
    runtime.parse_params()
    backends.load()  # raises BackendNotConfigured
    try:
        states.take("after", strict=True)
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return SkillOutcome.error(ErrorCode.STATE_UNAVAILABLE, describe_exception(e), observations={}, timing={})
    return SkillOutcome.ok(observations={}, timing={})


def main() -> None:
    states = StateSampler(reader=lambda: backends.load().sample_state(), prepare=lambda: backends.load().init_dds())
    runtime.run_main(POLICY.name, body, watchdog=False, states=states, setup=backends.setup_timing)


if __name__ == "__main__":
    main()
