"""The plan the model submits (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import StrictInt, StrictStr, model_validator

from ..models import Model

# the single source of the statuses; the submit_plan enum is derived from it (dispatcher/docs/loop-and-context.md)
PlanStatus = Literal["PLAN", "DONE", "ABORT", "ASK"]


class PlanStep(Model):
    skill: StrictStr
    params: dict[str, Any] = {}


class Plan(Model):
    status: PlanStatus
    steps: list[PlanStep] = []
    replan_after: StrictInt | None = None  # 1-based step index within this plan
    message: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_status(cls, data: Any) -> Any:
        # the API does not guarantee enum casing in non-strict mode
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            data = {**data, "status": data["status"].strip().upper()}
        return data
