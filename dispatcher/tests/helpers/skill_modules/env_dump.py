"""Test-only skill: reports the names of its environment variables (tests/docs/testing.md).

Run as ``python -m skill_modules.env_dump '{}'`` with ``dispatcher/tests/helpers`` on
``PYTHONPATH`` (importing it as ``skill_modules`` avoids loading ``helpers``).
"""

from __future__ import annotations

import os

from contract import policy
from contract.runtime import SkillOutcome
from skills.runner import run_skill

SKILL = "env_dump"


POLICY = policy.SkillPolicy(name=SKILL, timeout=10.0)


def _body(params: dict) -> SkillOutcome:
    return SkillOutcome.ok(
        observations={"env_keys": sorted(os.environ)}, timing={"client_init_ms": 0.0, "exec_ms": 0.0}
    )


if __name__ == "__main__":
    run_skill(POLICY, _body, sample_state=False)
