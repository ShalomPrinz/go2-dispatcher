"""Step-line rendering shared by the context builder and transports (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import json
from typing import Any, Iterable, Literal

from .bounds import cut_message
from .models import PlanStep, StepResult
from .registry import Registry

NOT_DISPATCHED_PREFIX = "- rejected before running: "
NOT_DISPATCHED_OUTCOMES = frozenset({"rejected", "motion_budget_exceeded"})


def format_value(v: Any) -> str:
    """Strings unquoted; numbers ``:g``; booleans ``true``/``false``; others ``json.dumps``."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _pairs(items: Iterable[tuple[str, Any]]) -> str:
    return ", ".join(f"{k}={format_value(v)}" for k, v in items)


def format_params(skill: str, params: dict[str, Any], registry: Registry) -> str:
    """Known skill: declared params in frontmatter order, then undeclared keys in received
    order. Unknown skill: received order."""
    desc = registry.get(skill)
    if desc is None:
        return _pairs(params.items())
    declared = [(k, params[k]) for k in desc.params if k in params]
    extra = [(k, v) for k, v in params.items() if k not in desc.params]
    return _pairs(declared + extra)


def format_call(skill: str, params: dict[str, Any], registry: Registry) -> str:
    return f"{skill}({format_params(skill, params, registry)})"


def _observations(sr: StepResult, registry: Registry) -> str:
    desc = registry.get(sr.skill)
    if desc is None or sr.response is None:
        return ""
    obs = sr.response.observations
    shown = [(k, obs[k]) for k in desc.policy.context_observations if k in obs]
    return ": " + _pairs(shown) if shown else ""


def _failure_text(sr: StepResult) -> str:
    msg = cut_message(sr.error_message or "")
    return f"{sr.outcome}: {msg}" if msg else sr.outcome


def render_step(sr: StepResult, registry: Registry, *, numbered: bool) -> str:
    """One line for a recorded step. ``numbered=False`` drops the ``{index}. `` prefix.
    Never includes stderr or tracebacks."""
    call = format_call(sr.skill, sr.params, registry)
    if sr.outcome in NOT_DISPATCHED_OUTCOMES or sr.index is None:
        return f"{NOT_DISPATCHED_PREFIX}{call} -> {_failure_text(sr)}"
    if sr.outcome == "ok":
        body = f"{call} -> ok{_observations(sr, registry)}"
    else:
        body = f"{call} -> {_failure_text(sr)}"
    return f"{sr.index}. {body}" if numbered else body


def render_remaining(plan_step: int, step: PlanStep, registry: Registry,
                     tag: Literal["pending", "abandoned"]) -> str:
    """A Remaining plan line (dispatcher/docs/loop-and-context.md); raw params."""
    return f"{plan_step}. {format_call(step.skill, step.params, registry)} [{tag}]"
