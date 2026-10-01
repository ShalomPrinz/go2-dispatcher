"""Skill policy base classes (docs/skills.md). Standard library only; re-exported by
``go2_dispatcher.policies``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MotionCost:
    distance_m: float = 0.0
    rotation_deg: float = 0.0


class SkillPolicy:
    """Per-skill dispatcher policy. Every number is a class attribute (docs/skills.md)."""

    name: str = ""
    context_observations: tuple[str, ...] = ()   # observation keys shown to the LLM on ok

    def timeout_s(self, params: dict) -> float:
        raise NotImplementedError

    def motion_cost(self, params: dict) -> MotionCost:
        return MotionCost()
