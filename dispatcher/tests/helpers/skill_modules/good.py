"""Valid test skill module: POLICY.name == "demo"."""

from skills.policy import SkillPolicy

POLICY = SkillPolicy(name="demo", timeout=5.0)
