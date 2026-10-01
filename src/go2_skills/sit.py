"""Skill: lower the body to the ground with StandDown (§8.3)."""

from __future__ import annotations

from go2_skills import motion, result
from go2_skills.policy_base import SkillPolicy

SKILL = "sit"


class SitPolicy(SkillPolicy):
    name = "sit"
    TIMEOUT_S = 15.0  # (sketch)
    SETTLE_S = 3.0    # wait after StandDown so state_after is sampled at rest (sketch; OD-10)

    def timeout_s(self, p):
        return self.TIMEOUT_S


POLICY = SitPolicy()


def body(params: dict):
    return motion.single_action("StandDown", SitPolicy.SETTLE_S)


def main() -> None:
    result.run_skill(SKILL, body)


if __name__ == "__main__":
    main()
