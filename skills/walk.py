"""Skill: walk in a straight line by a distance, then stop (skills/docs/skills.md)."""

from __future__ import annotations

from skills import motion, result

VELOCITY_MPS = 0.3
CMD_PERIOD_S = 0.1
# direction -> (vx, vy) as multiples of VELOCITY_MPS
DIRECTION_VECTORS = {"forward": (1, 0), "backward": (-1, 0), "left": (0, 1), "right": (0, -1)}
BASE_S = 10.0  # process start + SDK init + state samples (tunable)
FACTOR = 1.5  # safety factor on commanded motion time (tunable)

POLICY = result.SkillPolicy(
    name="walk",
    timeout=lambda p: BASE_S + FACTOR * p["distance_m"] / VELOCITY_MPS,
    cost=lambda p: result.MotionCost(distance_m=p["distance_m"]),
)


def body(params: dict):
    direction = params["direction"]
    distance_m = float(params["distance_m"])
    sx, sy = DIRECTION_VECTORS[direction]
    status, obs, code, msg, timing = motion.move_loop(
        sx * VELOCITY_MPS, sy * VELOCITY_MPS, 0.0, distance_m / VELOCITY_MPS, CMD_PERIOD_S
    )
    return status, {"direction": direction, "distance_m": distance_m, **obs}, code, msg, timing


def main() -> None:
    result.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
