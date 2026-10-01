"""The single place for the posture rule (skills/docs/robot.md)."""

from __future__ import annotations

POSTURE_SITTING_MAX_M = 0.15     # (tunable, skills/docs/robot.md)
POSTURE_STANDING_MIN_M = 0.22    # (tunable, skills/docs/robot.md)


def derive_posture(body_height: float | None, mode: int | None) -> str:
    """v1 rule: body_height < SITTING_MAX -> "sitting"; >= STANDING_MIN -> "standing";
    otherwise or None -> "unknown". `mode` is accepted but unused in v1, so the rule can
    change after the robot checklist without touching callers."""
    del mode  # unused in v1 (skills/docs/robot.md)
    if body_height is None:
        return "unknown"
    if body_height < POSTURE_SITTING_MAX_M:
        return "sitting"
    if body_height >= POSTURE_STANDING_MIN_M:
        return "standing"
    return "unknown"
