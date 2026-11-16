"""Reply checks and plan validation (dispatcher/docs/loop-and-context.md, dispatcher/docs/llm.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .. import prompts
from .plan import Plan

__all__ = ["RejectionKind", "ROOT_LOC", "validate_tool_input", "PlanCheck", "check_reply"]

RejectionKind = Literal["none", "schema", "horizon", "semantic", "no_tool_call", "max_tokens"]

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
        errors.append(prompts.PLAN_NEEDS_STEPS)
    if s in ("DONE", "ABORT", "ASK"):
        if n > 0:
            errors.append(prompts.STATUS_NO_STEPS.format(status=s))
        if s in ("ABORT", "ASK") and (plan.message is None or not plan.message.strip()):
            errors.append(prompts.STATUS_NEEDS_MESSAGE.format(status=s))
    if plan.replan_after is not None:
        if s != "PLAN":
            errors.append(prompts.REPLAN_ONLY_PLAN)
        elif not 1 <= plan.replan_after <= n:
            errors.append(prompts.REPLAN_OUT_OF_RANGE.format(n=n))
    return errors


def validate_tool_input(raw: Any, horizon: int) -> tuple[Plan | None, list[str], RejectionKind]:
    """Returns (plan, errors, rejection_kind). Plans are never truncated: an over-long plan is
    rejected as a whole."""
    errors: list[str] = []

    # 1. horizon, on the raw input, before pydantic
    kind: RejectionKind = "none"
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list) and len(raw["steps"]) > horizon:
        kind = "horizon"
        errors.append(prompts.HORIZON_EXCEEDED.format(n=len(raw["steps"]), horizon=horizon))

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

    # 4. rejection kind: horizon first
    if kind == "none" and schema_errors:
        kind = "schema"
    elif kind == "none" and semantic_errors:
        kind = "semantic"

    # 5. a plan only if there are no errors at all
    return (plan if not errors else None), errors, kind


class PlanCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Plan | None  # set iff errors is empty
    errors: list[str]
    rejection_kind: RejectionKind


def check_reply(tool_input: Any | None, *, cut_off: bool, horizon: int) -> PlanCheck:
    """Response handling then plan validation, in the order of dispatcher/docs/llm.md. ``tool_input`` is the raw
    submit_plan input (None without the call); ``cut_off`` is true when the reply hit the token limit."""
    if cut_off:
        return PlanCheck(plan=None, errors=[prompts.ERR_MAX_TOKENS], rejection_kind="max_tokens")
    if tool_input is None:
        return PlanCheck(plan=None, errors=[prompts.ERR_NO_TOOL_CALL], rejection_kind="no_tool_call")
    plan, errors, kind = validate_tool_input(tool_input, horizon)
    return PlanCheck(plan=plan, errors=errors, rejection_kind=kind)
