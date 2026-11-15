"""Skill: lower the body to the ground with StandDown (skills/docs/skills.md)."""

from __future__ import annotations

from contract import policy
from contract.runtime import SkillOutcome
from skills.runtime import motion, runner

TIMEOUT_S = 15.0  # (tunable)
SETTLE_S = 3.0  # wait after StandDown so state_after is sampled at rest (tunable, services/robot/docs/robot.md)

POLICY = policy.SkillPolicy(name="sit", timeout=TIMEOUT_S)


def body(params: dict) -> SkillOutcome:
    return motion.single_action("StandDown", SETTLE_S)


def main() -> None:
    runner.run_skill(POLICY, body)
