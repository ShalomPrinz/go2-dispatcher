"""Test skill module whose POLICY.name does not match its SKILL.md name."""

from contract.policy import SkillPolicy

POLICY = SkillPolicy(name="other", timeout=5.0)
