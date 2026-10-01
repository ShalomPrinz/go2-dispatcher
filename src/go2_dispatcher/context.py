"""User-message assembly from fixed slots (§11.3–§11.7). No conversation is carried."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from . import prompts
from .budget import MotionBudget
from .models import PlanStep, StepResult, TaskSummary
from .registry import Registry
from .render import render_remaining, render_step

BUDGET_DECIMALS = 2   # budget numbers: round(x, 2) then :g (§11.3)

Posture = Literal["standing", "sitting", "unknown"]
ReturnReason = Literal["initial", "plan_complete", "checkpoint", "failure"]


@dataclass(frozen=True)
class ContextInput:
    """Per-task state the user message is built from (a view of §15.2)."""

    task: str                                       # verbatim (after the transport's .strip())
    posture: Posture                                # last known posture (§11.5)
    budget: MotionBudget
    failures: int
    max_failures: int
    llm_calls: int                                  # completed LLM calls before this request
    max_llm_calls: int
    history_k: int                                  # cfg.loop.context_history_k
    return_reason: ReturnReason
    steps: Sequence[StepResult] = ()                # every recorded step of this task, in order
    remaining: Sequence[tuple[int, PlanStep]] = ()  # (plan_step, raw step) of the latest plan
    remaining_tag: Literal["pending", "abandoned"] | None = None
    notice_args: Mapping[str, Any] = field(default_factory=dict)  # n, skill, outcome, f
    previous: TaskSummary | None = None


def _num(x: float) -> str:
    return f"{round(x, BUDGET_DECIMALS):g}"


def _section(header: str, body: str) -> str:
    return f"{header}\n{body}"


def previous_task_block(previous: TaskSummary | None, registry: Registry) -> str:
    if previous is None:
        return prompts.NONE
    last = (render_step(previous.last_step, registry, numbered=False)
            if previous.last_step is not None else prompts.NONE)
    return prompts.PREVIOUS_TASK_TEMPLATE.format(
        task=previous.task, outcome=previous.outcome, message=previous.message, last_step=last)


def budget_block(inp: ContextInput) -> str:
    b = inp.budget
    return prompts.BUDGET_TEMPLATE.format(
        travel_used=_num(b.used_distance_m), travel_max=_num(b.max_distance_m),
        rotation_used=_num(b.used_rotation_deg), rotation_max=_num(b.max_rotation_deg),
        failures=inp.failures, max_failures=inp.max_failures,
        calls_made=inp.llm_calls, max_llm_calls=inp.max_llm_calls)


def executed_block(steps: Sequence[StepResult], registry: Registry, k: int) -> str:
    if not steps:
        return prompts.NOTHING_YET
    lines: list[str] = []
    if len(steps) > k:
        lines.append(prompts.OMITTED_ENTRIES.format(n=len(steps) - k))
        steps = steps[-k:]
    lines.extend(render_step(sr, registry, numbered=True) for sr in steps)
    return "\n".join(lines)


def remaining_block(remaining: Sequence[tuple[int, PlanStep]],
                    tag: Literal["pending", "abandoned"] | None, registry: Registry) -> str:
    if not remaining or tag is None:
        return prompts.NONE
    return "\n".join(render_remaining(n, step, registry, tag) for n, step in remaining)


def notice_block(inp: ContextInput) -> str:
    return prompts.notice(inp.return_reason, max_failures=inp.max_failures, **inp.notice_args)


def build_user_message(inp: ContextInput, registry: Registry) -> str:
    """The single user message (§11.3): sections in fixed order, one blank line between."""
    sections = [
        _section(prompts.SECTION_PREVIOUS_TASK, previous_task_block(inp.previous, registry)),
        _section(prompts.SECTION_ROBOT, prompts.POSTURE_TEMPLATE.format(posture=inp.posture)),
        _section(prompts.SECTION_TASK, inp.task),
        _section(prompts.SECTION_BUDGET, budget_block(inp)),
        _section(prompts.SECTION_EXECUTED, executed_block(inp.steps, registry, inp.history_k)),
        _section(prompts.SECTION_REMAINING,
                 remaining_block(inp.remaining, inp.remaining_tag, registry)),
        _section(prompts.SECTION_NOTICE, notice_block(inp)),
    ]
    return prompts.SECTION_SEPARATOR.join(sections)


def schema_retry_message(user: str, errors: list[str]) -> str:
    """Original user message, byte-identical, + ``"\\n\\n"`` + the Rejection section (§12.5)."""
    return user + prompts.SECTION_SEPARATOR + prompts.rejection_section(errors)
