"""Skill: turn in place by an angle, then stop (skills/docs/skills.md). Degrees in the interface."""

from __future__ import annotations

import math
from dataclasses import replace

from skills import motion, policy, ramp, runner
from skills.robot_ipc import YAW_SIGN, MoveTarget

YAW_RATE_RPS = 1.0  # rad/s
CAP_FACTOR = 1.25  # cap on the closed-loop turn: nominal time x factor + CAP_S (tunable, skills/docs/robot.md)
CAP_S = 0.5  # s, covers the start ramp (tunable)
LEAD_S = ramp.YAW_S  # start-ramp time of an open-loop turn, when no fresh yaw is readable at the start (robot.md)


def _motion_s(params: dict) -> float:
    return math.radians(float(params["angle_deg"])) / YAW_RATE_RPS * CAP_FACTOR + CAP_S


POLICY = policy.SkillPolicy(
    name="turn",
    timeout=lambda p: motion.motion_timeout(_motion_s(p)),
    cost=lambda p: policy.MotionCost(rotation_deg=p["angle_deg"]),
    context_observations=("closed_loop", "turned_deg"),
)


def body(params: dict) -> runner.SkillOutcome:
    direction = params["direction"]
    angle_deg = float(params["angle_deg"])
    sign = YAW_SIGN[direction]
    target = MoveTarget(yaw_rad=sign * math.radians(angle_deg), lead_s=LEAD_S)
    outcome = motion.move(motion.Velocity(0.0, 0.0, sign * YAW_RATE_RPS), _motion_s(params), target)
    return replace(outcome, observations={"direction": direction, "angle_deg": angle_deg, **outcome.observations})


def main() -> None:
    runner.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
