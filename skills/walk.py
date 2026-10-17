"""Skill: walk in a straight line by a distance, then stop (skills/docs/skills.md)."""

from __future__ import annotations

from dataclasses import replace

from skills import motion, policy, runner

VELOCITY_MPS = 0.3
# direction -> (vx, vy) as multiples of VELOCITY_MPS
DIRECTION_VECTORS = {"forward": (1, 0), "backward": (-1, 0), "left": (0, 1), "right": (0, -1)}


def _motion_s(params: dict) -> float:
    return float(params["distance_m"]) / VELOCITY_MPS


POLICY = policy.SkillPolicy(
    name="walk",
    timeout=lambda p: motion.motion_timeout(_motion_s(p)),
    cost=lambda p: policy.MotionCost(distance_m=p["distance_m"]),
)


def body(params: dict) -> runner.SkillOutcome:
    direction = params["direction"]
    distance_m = float(params["distance_m"])
    sx, sy = DIRECTION_VECTORS[direction]
    outcome = motion.move_loop(motion.Velocity(sx * VELOCITY_MPS, sy * VELOCITY_MPS, 0.0), _motion_s(params))
    return replace(outcome, observations={"direction": direction, "distance_m": distance_m, **outcome.observations})


def main() -> None:
    runner.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
