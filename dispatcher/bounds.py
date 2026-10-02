"""Step bounds and the whole-plan pre-check (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .budget import MotionBudget
from .models import Plan, PlanStep, StepResult
from .registry import ParamSpec, Registry

# StepResult.error_message limit (dispatcher/docs/run-log.md, dispatcher/docs/loop-and-context.md)
ERROR_MESSAGE_MAX = 200
ELLIPSIS = "…"
BOUNDS_ERROR_CODE = "bounds"
BUDGET_ERROR_CODE = "motion_budget_exceeded"


def cut_message(text: str, limit: int = ERROR_MESSAGE_MAX) -> str:
    """One line, at most ``limit`` chars (longer is cut to ``limit - 1`` + ``…``)."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + ELLIPSIS


# --- step bounds -------------------------------------------------------------------

_MISSING = object()
_OVERFLOW = object()  # a number too large to convert to float (e.g. 10**400)
REPR_MAX = 40  # max chars of a bad value's repr in a violation


def _short_repr(value: Any) -> str:
    r = repr(value)
    return r if len(r) <= REPR_MAX else r[: REPR_MAX - 1] + ELLIPSIS


def _finite(value: int | float) -> bool | None:
    """True/False for finite/non-finite; None if the value overflows float conversion."""
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return None


def _expected(spec: ParamSpec) -> str:
    if spec.type == "number":
        return "a finite number"
    if spec.type == "integer":
        return "an integer"
    if spec.type == "string":
        return "a non-empty string"
    return "one of " + ", ".join(spec.values or ())


def _coerce(spec: ParamSpec, value: Any) -> Any:
    """Type check without other coercion (dispatcher/docs/loop-and-context.md).

    Returns the normalised value or _MISSING.
    """
    if spec.type in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _MISSING
        finite = _finite(value)
        if finite is None:
            return _OVERFLOW
        if not finite:
            return _MISSING
        if spec.type == "number":
            return value
        if isinstance(value, int):
            return value
        return int(value) if value.is_integer() else _MISSING
    if not isinstance(value, str):
        return _MISSING
    if spec.type == "string":
        stripped = value.strip()
        return stripped if stripped else _MISSING
    normalised = value.strip().lower()  # enum
    return normalised if normalised in (spec.values or ()) else _MISSING


def _range_violation(pname: str, skill: str, spec: ParamSpec, v: float) -> str | None:
    lo, hi = spec.min, spec.max
    below = lo is not None and v < lo
    above = hi is not None and v > hi
    if not (below or above):
        return None
    where = f"parameter '{pname}' for skill {skill} is {v:g}"
    if lo is not None and hi is not None:
        return f"{where}, outside {lo:g} to {hi:g}"
    if below:
        return f"{where}, below the minimum {lo:g}"
    return f"{where}, above the maximum {hi:g}"


def check_step(step: PlanStep, registry: Registry) -> tuple[dict | None, list[str]]:
    """Returns (filled_params, violations); filled_params is None when there are violations."""
    s = step.skill
    desc = registry.get(s)
    if desc is None:
        return None, [f"unknown skill '{s}'; available: {', '.join(sorted(registry.names()))}"]

    raw = step.params
    violations: list[str] = []
    for p in raw:
        if p not in desc.params:
            violations.append(f"unknown parameter '{p}' for skill {s}")
    for p, spec in desc.params.items():
        if spec.required and p not in raw:
            violations.append(f"missing parameter '{p}' for skill {s}")

    typed: dict[str, Any] = {}
    for p, spec in desc.params.items():
        if p not in raw:
            continue
        value = _coerce(spec, raw[p])
        if value is _MISSING or value is _OVERFLOW:
            expected = "a finite number" if value is _OVERFLOW else _expected(spec)
            violations.append(f"parameter '{p}' for skill {s} must be {expected}, got {_short_repr(raw[p])}")
        else:
            typed[p] = value

    for p, value in typed.items():
        spec = desc.params[p]
        if spec.type in ("number", "integer"):
            v = _range_violation(p, s, spec, value)
            if v:
                violations.append(v)

    if violations:
        return None, violations

    filled: dict[str, Any] = {}
    for p, spec in desc.params.items():  # frontmatter order
        filled[p] = typed[p] if p in typed else spec.default
    return filled, []


# --- whole-plan pre-check ----------------------------------------------------------


@dataclass
class PrecheckResult:
    rejection: StepResult | None
    filled: list[dict] = field(default_factory=list)  # filled params for every plan step; empty if rejected


def precheck(plan: Plan, stop_at: int, registry: Registry, budget: MotionBudget, call_index: int) -> PrecheckResult:
    """Bounds-check every step; then simulate the motion budget over steps 1..stop_at on a
    copy of ``budget``. A plan either runs within limits or does not start (dispatcher/docs/loop-and-context.md)."""
    filled_all: list[dict] = []
    for i, step in enumerate(plan.steps, start=1):
        filled, violations = check_step(step, registry)
        if violations:
            return PrecheckResult(
                rejection=StepResult(
                    index=None,
                    call_index=call_index,
                    plan_step=i,
                    skill=step.skill,
                    params=dict(step.params),
                    outcome="rejected",
                    error_code=BOUNDS_ERROR_CODE,
                    error_message=cut_message("; ".join(violations)),
                ),
                filled=[],
            )
        filled_all.append(filled)

    sim = budget.copy()
    for i, (step, filled) in enumerate(zip(plan.steps[:stop_at], filled_all, strict=False), start=1):
        cost = registry.get(step.skill).policy.motion_cost(filled)
        kind = sim.would_exceed(cost)
        if kind is not None:
            return PrecheckResult(
                rejection=StepResult(
                    index=None,
                    call_index=call_index,
                    plan_step=i,
                    skill=step.skill,
                    params=dict(filled),
                    outcome="motion_budget_exceeded",
                    error_code=BUDGET_ERROR_CODE,
                    error_message=cut_message(sim.exceeded_message(kind, cost)),
                ),
                filled=[],
            )
        sim.charge(cost)
    return PrecheckResult(rejection=None, filled=filled_all)
