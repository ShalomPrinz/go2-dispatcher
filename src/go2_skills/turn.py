"""Skill: turn in place by an angle, then stop (docs/skills.md). Degrees in the interface."""

from __future__ import annotations

import math

from go2_skills import motion, result
from go2_skills.policy_base import MotionCost, SkillPolicy

SKILL = "turn"
YAW_RATE_RPS = 1.0  # rad/s
CMD_PERIOD_S = 0.1
SDK_TIMEOUT_S = 10.0  # applied by the real backend's SportClient.SetTimeout
DIRECTIONS = ("left", "right")
DIRECTION_SIGN = {"left": 1, "right": -1}


class TurnPolicy(SkillPolicy):
    name = "turn"
    BASE_S = 10.0     # process start + SDK init + state samples (tunable)
    FACTOR = 1.5      # safety factor on commanded motion time (tunable)

    def timeout_s(self, p):
        return self.BASE_S + self.FACTOR * math.radians(p["angle_deg"]) / YAW_RATE_RPS

    def motion_cost(self, p):
        return MotionCost(rotation_deg=p["angle_deg"])


POLICY = TurnPolicy()


def body(params: dict):
    direction = motion.require_enum(params, "direction", DIRECTIONS)
    angle_deg = motion.require_number(params, "angle_deg")
    status, obs, code, msg, timing = motion.move_loop(
        0.0, 0.0, DIRECTION_SIGN[direction] * YAW_RATE_RPS,
        math.radians(angle_deg) / YAW_RATE_RPS, CMD_PERIOD_S)
    return status, {"direction": direction, "angle_deg": angle_deg, **obs}, code, msg, timing


def main() -> None:
    result.run_skill(SKILL, body)


if __name__ == "__main__":
    main()
