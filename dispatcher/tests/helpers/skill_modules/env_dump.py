"""Test-only skill: reports the names of its environment variables (tests/docs/testing.md).

Run as ``python -m skill_modules.env_dump '{}'`` with ``dispatcher/tests/helpers`` on
``PYTHONPATH`` (importing it as ``skill_modules`` avoids loading ``helpers``).
"""

from __future__ import annotations

import os

from skills import result

SKILL = "env_dump"


POLICY = result.SkillPolicy(name=SKILL, timeout=10.0)


def _body(params: dict) -> result.SkillOutcome:
    return result.SkillOutcome.ok(
        observations={"env_keys": sorted(os.environ)}, timing={"init_ms": 0.0, "exec_ms": 0.0}
    )


if __name__ == "__main__":
    result.run_skill(POLICY, _body, sample_state=False)
