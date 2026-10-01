"""Test skill module whose POLICY.name does not match its SKILL.md name."""

from skills.policy_base import SkillPolicy


class OtherPolicy(SkillPolicy):
    name = "other"


POLICY = OtherPolicy()
