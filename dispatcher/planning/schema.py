"""The submit_plan tool schema (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

from typing import get_args

from .plan import PlanStatus

__all__ = ["TOOL_NAME", "TOOL_REQUIRED", "STEP_REQUIRED", "plan_tool_schema"]

TOOL_NAME = "submit_plan"
TOOL_REQUIRED = ["status", "steps"]
STEP_REQUIRED = ["skill", "params"]


def plan_tool_schema(horizon: int) -> dict:
    """The hand-written submit_plan tool definition, with ``maxItems = horizon``."""
    return {
        "name": TOOL_NAME,
        "description": "Submit your plan for the current task. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": list(get_args(PlanStatus)),
                    "description": "PLAN: run the steps. DONE: task complete, no steps. "
                    "ABORT: cannot or should not be done, no steps. "
                    "ASK: ambiguous, message is a question for the operator, no steps.",
                },
                "steps": {
                    "type": "array",
                    "maxItems": horizon,
                    "description": "Skill calls to run in order. Empty for DONE, ABORT and ASK.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "skill": {"type": "string", "description": "Skill name from the skill list."},
                            "params": {"type": "object", "description": "Parameter values for this skill."},
                        },
                        "required": list(STEP_REQUIRED),
                        "additionalProperties": False,
                    },
                },
                "replan_after": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional. Stop after this step number and call again before running the rest.",
                },
                "message": {
                    "type": "string",
                    "description": "Required for DONE, ABORT and ASK: text for the operator. Optional for PLAN.",
                },
            },
            "required": list(TOOL_REQUIRED),
            "additionalProperties": False,
        },
    }
