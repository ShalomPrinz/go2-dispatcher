"""Plan-level validation of the raw submit_plan tool input (dispatcher/docs/loop-and-context.md).

Plans are never truncated: an over-long plan is rejected as a whole.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

from .models import Plan

__all__ = ["RejectionKind", "ROOT_LOC", "validate_tool_input"]

RejectionKind = Literal["none", "schema", "horizon", "semantic"]

# loc label for a pydantic error that has no location (the whole input is wrong)
ROOT_LOC = "input"


def _schema_errors(e: ValidationError) -> list[str]:
    out: list[str] = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or ROOT_LOC
        out.append(f"{loc}: {err['msg']}")
    return out


def _semantic_errors(plan: Plan) -> list[str]:
    errors: list[str] = []
    s = plan.status
    n = len(plan.steps)
    if s == "PLAN" and n == 0:
        errors.append("status PLAN needs at least one step")
    if s in ("DONE", "ABORT"):
        if n > 0:
            errors.append(f"status {s} must have no steps")
        if plan.message is None or not plan.message.strip():
            errors.append(f"status {s} needs a message")
    if plan.replan_after is not None:
        if s != "PLAN":
            errors.append("replan_after is only allowed with status PLAN")
        elif not 1 <= plan.replan_after <= n:
            errors.append(f"replan_after must be between 1 and {n}")
    return errors


def validate_tool_input(raw: Any, horizon: int) -> tuple[Plan | None, list[str], bool, str]:
    """Returns (plan, errors, horizon_exceeded, rejection_kind)."""
    errors: list[str] = []

    # 1. horizon, on the raw input, before pydantic
    horizon_exceeded = False
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list) and len(raw["steps"]) > horizon:
        horizon_exceeded = True
        errors.append(f"plan has {len(raw['steps'])} steps; the maximum is {horizon}")

    # 2. schema
    plan: Plan | None = None
    schema_errors: list[str] = []
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as e:
        schema_errors = _schema_errors(e)
    errors.extend(schema_errors)

    # 3. semantic, only if the schema passed
    semantic_errors = _semantic_errors(plan) if plan is not None else []
    errors.extend(semantic_errors)

    # 5. rejection kind
    if horizon_exceeded:
        kind = "horizon"
    elif schema_errors:
        kind = "schema"
    elif semantic_errors:
        kind = "semantic"
    else:
        kind = "none"

    # 4. a plan only if there are no errors at all
    return (plan if not errors else None), errors, horizon_exceeded, kind
