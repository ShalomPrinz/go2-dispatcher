"""Skill: the robot's built-in stretch routine (skills/docs/skills.md)."""

from __future__ import annotations

from skills import motion, result

TIMEOUT_S = 20.0  # (tunable)
SETTLE_S = 6.0  # wait after Stretch so state_after is sampled at rest (tunable, skills/docs/robot.md)

POLICY = result.SkillPolicy(name="stretch", timeout=TIMEOUT_S)


def body(params: dict):
    return motion.single_action("Stretch", SETTLE_S)


def main() -> None:
    result.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
