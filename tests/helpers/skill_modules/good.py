"""Valid test skill module: POLICY.name == "demo"."""

from skills.policy_base import SkillPolicy


class DemoPolicy(SkillPolicy):
    name = "demo"
    TIMEOUT_S = 5.0

    def timeout_s(self, params):
        return self.TIMEOUT_S


POLICY = DemoPolicy()
