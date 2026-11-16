"""Test-only skill: reports the names of its environment variables (tests/docs/testing.md).

Run as ``python -m tests.integration.env_dump '{}'``; it lives here, not under
``dispatcher``, because it imports ``skills.runtime`` (the dispatcher imports no skill).
"""

from __future__ import annotations

import os

from contract import policy
from contract.runtime import SkillOutcome
from skills.runtime.runner import run_skill

SKILL = "env_dump"


POLICY = policy.SkillPolicy(name=SKILL, timeout=10.0)


def _body(params: dict) -> SkillOutcome:
    return SkillOutcome.ok(
        observations={"env_keys": sorted(os.environ)}, timing={"client_init_ms": 0.0, "exec_ms": 0.0}
    )


if __name__ == "__main__":
    run_skill(POLICY, _body, sample_state=False)
