"""Skill: turn in place by an angle, then stop (skills/docs/skills.md). Degrees in the interface."""

from __future__ import annotations

import math
from dataclasses import replace

from skills import motion, policy, runner

YAW_RATE_RPS = 1.0  # rad/s
DIRECTION_SIGN = {"left": 1, "right": -1}


def _motion_s(params: dict) -> float:
    return math.radians(float(params["angle_deg"])) / YAW_RATE_RPS


POLICY = policy.SkillPolicy(
    name="turn",
    timeout=lambda p: motion.motion_timeout(_motion_s(p)),
    cost=lambda p: policy.MotionCost(rotation_deg=p["angle_deg"]),
)


def body(params: dict) -> runner.SkillOutcome:
    direction = params["direction"]
    angle_deg = float(params["angle_deg"])
    outcome = motion.move_loop(motion.Velocity(0.0, 0.0, DIRECTION_SIGN[direction] * YAW_RATE_RPS), _motion_s(params))
    return replace(outcome, observations={"direction": direction, "angle_deg": angle_deg, **outcome.observations})


def main() -> None:
    runner.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
