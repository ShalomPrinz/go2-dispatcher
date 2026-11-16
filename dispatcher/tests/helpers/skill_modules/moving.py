"""Valid test skill module with a motion cost: POLICY.name == "demo"."""

from contract.policy import MotionCost, SkillPolicy

POLICY = SkillPolicy(name="demo", timeout=5.0, cost=lambda p: MotionCost(distance_m=p["speed"]))
