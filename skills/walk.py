"""Skill: walk in a straight line by a distance, then stop (skills/docs/skills.md)."""

from __future__ import annotations

from skills import motion, result
from skills.policy_base import MotionCost, SkillPolicy

SKILL = "walk"
VELOCITY_MPS = 0.3
CMD_PERIOD_S = 0.1
SDK_TIMEOUT_S = 10.0  # applied by the real backend's SportClient.SetTimeout
DIRECTIONS = ("forward", "backward", "left", "right")
# direction -> (vx, vy) as multiples of VELOCITY_MPS
DIRECTION_VECTORS = {"forward": (1, 0), "backward": (-1, 0), "left": (0, 1), "right": (0, -1)}


class WalkPolicy(SkillPolicy):
    name = "walk"
    BASE_S = 10.0     # process start + SDK init + state samples (tunable)
    FACTOR = 1.5      # safety factor on commanded motion time (tunable)

    def timeout_s(self, p):
        return self.BASE_S + self.FACTOR * p["distance_m"] / VELOCITY_MPS

    def motion_cost(self, p):
        return MotionCost(distance_m=p["distance_m"])


POLICY = WalkPolicy()


def body(params: dict):
    direction = motion.require_enum(params, "direction", DIRECTIONS)
    distance_m = motion.require_number(params, "distance_m")
    sx, sy = DIRECTION_VECTORS[direction]
    status, obs, code, msg, timing = motion.move_loop(
        sx * VELOCITY_MPS, sy * VELOCITY_MPS, 0.0, distance_m / VELOCITY_MPS, CMD_PERIOD_S)
    return status, {"direction": direction, "distance_m": distance_m, **obs}, code, msg, timing


def main() -> None:
    result.run_skill(SKILL, body)


if __name__ == "__main__":
    main()
