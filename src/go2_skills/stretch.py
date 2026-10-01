"""Skill: the robot's built-in stretch routine (§8.4)."""

from __future__ import annotations

from go2_skills import motion, result
from go2_skills.policy_base import SkillPolicy

SKILL = "stretch"


class StretchPolicy(SkillPolicy):
    name = "stretch"
    TIMEOUT_S = 20.0  # (sketch)
    SETTLE_S = 6.0    # wait after Stretch so state_after is sampled at rest (sketch; OD-10)

    def timeout_s(self, p):
        return self.TIMEOUT_S


POLICY = StretchPolicy()


def body(params: dict):
    return motion.single_action("Stretch", StretchPolicy.SETTLE_S)


def main() -> None:
    result.run_skill(SKILL, body)


if __name__ == "__main__":
    main()
