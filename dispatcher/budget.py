"""Per-task motion budget on commanded motion (docs/safety.md)."""

from __future__ import annotations

from typing import Literal

from skills.result import MotionCost

BUDGET_EPSILON = 1e-9  # tolerance on the limit: exceeds when used + cost > max + epsilon
# numbers in the motion-budget message are rounded to this (dispatcher/docs/loop-and-context.md)
MESSAGE_DECIMALS = 2

# Motion-budget message (dispatcher/docs/loop-and-context.md); kind -> unit
MOTION_BUDGET_MESSAGE = "this step needs {need:g} {unit} of {kind} but only {left:g} {unit} remain for this task"
KIND_UNITS = {"travel": "m", "rotation": "deg"}

BudgetKind = Literal["travel", "rotation"]


class MotionBudget:
    """One per task, starting at zero. ``charge()`` on dispatch, however the step ends."""

    def __init__(self, max_distance_m: float, max_rotation_deg: float):
        self.max_distance_m = max_distance_m
        self.max_rotation_deg = max_rotation_deg
        self.used_distance_m = 0.0
        self.used_rotation_deg = 0.0

    def would_exceed(self, cost: MotionCost) -> BudgetKind | None:
        """Travel is checked before rotation."""
        if self.used_distance_m + cost.distance_m > self.max_distance_m + BUDGET_EPSILON:
            return "travel"
        if self.used_rotation_deg + cost.rotation_deg > self.max_rotation_deg + BUDGET_EPSILON:
            return "rotation"
        return None

    def charge(self, cost: MotionCost) -> None:
        self.used_distance_m += cost.distance_m
        self.used_rotation_deg += cost.rotation_deg

    def copy(self) -> MotionBudget:
        other = MotionBudget(self.max_distance_m, self.max_rotation_deg)
        other.used_distance_m = self.used_distance_m
        other.used_rotation_deg = self.used_rotation_deg
        return other

    def remaining(self, kind: BudgetKind) -> float:
        if kind == "travel":
            return max(0.0, self.max_distance_m - self.used_distance_m)
        return max(0.0, self.max_rotation_deg - self.used_rotation_deg)

    def exceeded_message(self, kind: BudgetKind, cost: MotionCost) -> str:
        """The ``error_message`` for a step of ``cost`` that would exceed ``kind``.

        (dispatcher/docs/loop-and-context.md)
        """
        need = cost.distance_m if kind == "travel" else cost.rotation_deg
        return MOTION_BUDGET_MESSAGE.format(
            need=round(need, MESSAGE_DECIMALS),
            left=round(self.remaining(kind), MESSAGE_DECIMALS),
            unit=KIND_UNITS[kind],
            kind=kind,
        )

    def used(self) -> dict[str, float]:
        """The ``budget_used`` field of the run log (dispatcher/docs/run-log.md)."""
        return {"distance_m": self.used_distance_m, "rotation_deg": self.used_rotation_deg}
