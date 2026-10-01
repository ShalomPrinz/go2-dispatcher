"""Test-only skill: reports the names of its environment variables (docs/testing.md).

Run as ``python -m skill_modules.env_dump '{}'`` with ``tests/helpers`` on
``PYTHONPATH`` (importing it as ``skill_modules`` avoids loading ``helpers``).
"""

from __future__ import annotations

import os

from go2_skills import result
from go2_skills.policy_base import SkillPolicy

SKILL = "env_dump"


class EnvDumpPolicy(SkillPolicy):
    name = SKILL
    TIMEOUT_S = 10.0

    def timeout_s(self, params):
        return self.TIMEOUT_S


POLICY = EnvDumpPolicy()


def _body(params: dict):
    return "ok", {"env_keys": sorted(os.environ)}, None, None, {"init_ms": 0.0, "exec_ms": 0.0}


if __name__ == "__main__":
    result.run_skill(SKILL, _body, sample_state=False)
