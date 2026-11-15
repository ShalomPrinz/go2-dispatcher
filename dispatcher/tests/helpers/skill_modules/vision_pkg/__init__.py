"""Valid test skill package whose vision client sits in a submodule, as in skills/<name>/: POLICY.name == "demo"."""

from dispatcher.tests.helpers.skill_modules.vision_pkg.skill import POLICY

__all__ = ["POLICY"]
