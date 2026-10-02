"""Valid test skill module: POLICY.name == "demo"."""

from skills.result import SkillPolicy

POLICY = SkillPolicy(name="demo", timeout=5.0)
