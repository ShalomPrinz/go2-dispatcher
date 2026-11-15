"""The code of the vision_pkg test skill: holds the vision client as a module global."""

from contract.policy import SkillPolicy
from services.vision import client

POLICY = SkillPolicy(name="demo", timeout=5.0)
CLIENT = client
