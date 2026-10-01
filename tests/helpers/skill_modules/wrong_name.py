"""Test skill module whose POLICY.name does not match its SKILL.md name."""

from go2_skills.policy_base import SkillPolicy


class OtherPolicy(SkillPolicy):
    name = "other"


POLICY = OtherPolicy()
