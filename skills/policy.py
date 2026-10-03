"""Per-skill dispatcher policy: SkillPolicy and MotionCost, read by the dispatcher from each
skill module's ``POLICY`` (skills/docs/skills.md). Standard library only."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class MotionCost:
    distance_m: float = 0.0
    rotation_deg: float = 0.0

    def __post_init__(self):  # plan params may be ints; the run log records floats
        object.__setattr__(self, "distance_m", float(self.distance_m))
        object.__setattr__(self, "rotation_deg", float(self.rotation_deg))


@dataclass(frozen=True)
class SkillPolicy:
    """Per-skill dispatcher policy, one module-level ``POLICY`` per skill (skills/docs/skills.md).
    ``timeout`` and ``cost`` are a constant or a function of the validated params; the dispatcher
    reads them only through ``timeout_s(params)`` and ``motion_cost(params)``."""

    name: str
    timeout: float | Callable[[dict], float]
    cost: MotionCost | Callable[[dict], MotionCost] = MotionCost()
    context_observations: tuple[str, ...] = ()  # observation keys shown to the LLM on ok

    def timeout_s(self, params: dict) -> float:
        return self.timeout(params) if callable(self.timeout) else self.timeout

    def motion_cost(self, params: dict) -> MotionCost:
        return self.cost(params) if callable(self.cost) else self.cost
