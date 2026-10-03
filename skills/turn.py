"""Skill: turn in place by an angle, then stop (skills/docs/skills.md). Degrees in the interface."""

from __future__ import annotations

import math
from dataclasses import replace

from skills import motion, result

YAW_RATE_RPS = 1.0  # rad/s
CMD_PERIOD_S = 0.1
DIRECTION_SIGN = {"left": 1, "right": -1}
BASE_S = 10.0  # process start + SDK init + state samples (tunable)
FACTOR = 1.5  # safety factor on commanded motion time (tunable)

POLICY = result.SkillPolicy(
    name="turn",
    timeout=lambda p: BASE_S + FACTOR * math.radians(p["angle_deg"]) / YAW_RATE_RPS,
    cost=lambda p: result.MotionCost(rotation_deg=p["angle_deg"]),
)


def body(params: dict) -> result.SkillOutcome:
    direction = params["direction"]
    angle_deg = float(params["angle_deg"])
    outcome = motion.move_loop(
        0.0, 0.0, DIRECTION_SIGN[direction] * YAW_RATE_RPS, math.radians(angle_deg) / YAW_RATE_RPS, CMD_PERIOD_S
    )
    return replace(outcome, observations={"direction": direction, "angle_deg": angle_deg, **outcome.observations})


def main() -> None:
    result.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
