"""Skill: lower the body to the ground with StandDown (skills/docs/skills.md)."""

from __future__ import annotations

from skills import motion, policy, runner

TIMEOUT_S = 15.0  # (tunable)
SETTLE_S = 3.0  # wait after StandDown so state_after is sampled at rest (tunable, skills/docs/robot.md)

POLICY = policy.SkillPolicy(name="sit", timeout=TIMEOUT_S)


def body(params: dict) -> runner.SkillOutcome:
    return motion.single_action("StandDown", SETTLE_S)


def main() -> None:
    runner.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
