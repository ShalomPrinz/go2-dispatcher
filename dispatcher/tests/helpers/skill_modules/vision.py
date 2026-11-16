"""Valid test skill module that is a vision service client: POLICY.name == "demo"."""

from contract.policy import SkillPolicy
from services.vision import client

POLICY = SkillPolicy(name="demo", timeout=5.0)
CLIENT = client  # the registry checks the module global, not its name
