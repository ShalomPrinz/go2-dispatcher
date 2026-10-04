"""User-message assembly from fixed slots and step-line rendering shared with the transports
(dispatcher/docs/loop-and-context.md). No conversation is carried."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from . import prompts
from .bounds import cut_message
from .budget import MotionBudget
from .llm import plan_tool_schema
from .models import PlanStep, Posture, StepResult, TaskSummary
from .registry import Registry, registry_hash

NOT_DISPATCHED_PREFIX = "- rejected before running: "
NOT_DISPATCHED_OUTCOMES = frozenset({"rejected", "motion_budget_exceeded"})
BUDGET_DECIMALS = 2  # budget numbers: round(x, 2) then :g (dispatcher/docs/loop-and-context.md)

ReturnReason = Literal["initial", "plan_complete", "checkpoint", "failure"]
RequestReason = ReturnReason | Literal["schema_retry"]  # logged on llm_request (dispatcher/docs/run-log.md)
RemainingTag = Literal["pending", "abandoned"]


# --- prompt surface (dispatcher/docs/loop-and-context.md) ----------------------------------


@dataclass(frozen=True)
class PromptSurface:
    """The fixed request parts, built once per process: sent by the Dispatcher, copied into the run log."""

    system: tuple[str, str]
    catalog_text: str
    tool_schema: dict[str, Any]
    registry_hash: str
    skills: tuple[str, ...]

    @classmethod
    def build(cls, registry: Registry, horizon: int) -> PromptSurface:
        catalog_text = registry.catalog_text()
        system_text, skills_text = prompts.system_blocks(horizon, catalog_text)
        tool_schema = plan_tool_schema(horizon)
        return cls(
            system=(system_text, skills_text),
            catalog_text=catalog_text,
            tool_schema=tool_schema,
            registry_hash=registry_hash(system_text, catalog_text, tool_schema),
            skills=tuple(registry.names()),
        )


# --- step-line rendering (dispatcher/docs/loop-and-context.md) -----------------------------


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
    desc = registry.get(sr.ref.skill)
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
    call = format_call(sr.ref.skill, sr.ref.params, registry)
    if sr.outcome in NOT_DISPATCHED_OUTCOMES or sr.dispatch is None:
        return f"{NOT_DISPATCHED_PREFIX}{call} -> {_failure_text(sr)}"
    if sr.outcome == "ok":
        body = f"{call} -> ok{_observations(sr, registry)}"
    else:
        body = f"{call} -> {_failure_text(sr)}"
    return f"{sr.dispatch.index}. {body}" if numbered else body


def render_remaining(plan_step: int, step: PlanStep, registry: Registry, tag: RemainingTag) -> str:
    """A Remaining plan line (dispatcher/docs/loop-and-context.md); raw params."""
    return f"{plan_step}. {format_call(step.skill, step.params, registry)} [{tag}]"


# --- user message ----------------------------------------------------------------------------


def _numbered_after(steps: Sequence[PlanStep], k: int) -> tuple[tuple[int, PlanStep], ...]:
    return tuple((j, s) for j, s in enumerate(steps, start=1) if j > k)


@dataclass(frozen=True)
class Feedback:
    """What the next call is told about the last plan: return reason, Remaining plan and notice values
    (dispatcher/docs/loop-and-context.md). Build it with the constructor for its reason."""

    return_reason: ReturnReason = "initial"
    remaining: tuple[tuple[int, PlanStep], ...] = ()  # (plan_step, raw step) of the latest plan
    remaining_tag: RemainingTag | None = None
    stop_at: int | None = None  # checkpoint
    failed: StepResult | None = None  # failure: the failed or rejected step
    failures: int = 0  # failure: failure count including it

    @classmethod
    def plan_complete(cls) -> Feedback:
        return cls("plan_complete")

    @classmethod
    def checkpoint(cls, steps: Sequence[PlanStep], stop_at: int) -> Feedback:
        return cls("checkpoint", _numbered_after(steps, stop_at), "pending", stop_at=stop_at)

    @classmethod
    def failure(cls, steps: Sequence[PlanStep], failed: StepResult, failures: int, *, ran: int) -> Feedback:
        """``ran``: plan steps that ran, the failed one included (0 for a pre-check rejection)."""
        return cls("failure", _numbered_after(steps, ran), "abandoned", failed=failed, failures=failures)

    def notice(self, max_failures: int) -> str:
        if self.return_reason == "checkpoint":
            return prompts.notice("checkpoint", n=self.stop_at)
        if self.return_reason == "failure":
            assert self.failed is not None  # set by Feedback.failure
            ref = self.failed.ref
            return prompts.notice(
                "failure",
                n=ref.plan_step,
                skill=ref.skill,
                outcome=self.failed.outcome,
                f=self.failures,
                max_failures=max_failures,
            )
        return prompts.notice(self.return_reason)


@dataclass(frozen=True)
class ContextInput:
    """Per-task state the user message is built from (dispatcher/docs/loop-and-context.md)."""

    task: str  # verbatim (after the transport's .strip())
    posture: Posture  # last known posture (dispatcher/docs/loop-and-context.md)
    budget: MotionBudget
    failures: int
    max_failures: int
    llm_calls: int  # completed LLM calls before this request
    max_llm_calls: int
    history_k: int  # cfg.loop.context_history_k
    steps: Sequence[StepResult] = ()  # every recorded step of this task, in order
    feedback: Feedback = Feedback()
    previous: TaskSummary | None = None


def _num(x: float) -> str:
    return f"{round(x, BUDGET_DECIMALS):g}"


def _section(header: str, body: str) -> str:
    return f"{header}\n{body}"


def previous_task_block(previous: TaskSummary | None, registry: Registry) -> str:
    if previous is None:
        return prompts.NONE
    last = render_step(previous.last_step, registry, numbered=False) if previous.last_step is not None else prompts.NONE
    return prompts.PREVIOUS_TASK_TEMPLATE.format(
        task=previous.task, outcome=previous.outcome, message=previous.message, last_step=last
    )


def budget_block(inp: ContextInput) -> str:
    b = inp.budget
    return prompts.BUDGET_TEMPLATE.format(
        travel_used=_num(b.used_distance_m),
        travel_max=_num(b.max_distance_m),
        rotation_used=_num(b.used_rotation_deg),
        rotation_max=_num(b.max_rotation_deg),
        failures=inp.failures,
        max_failures=inp.max_failures,
        calls_made=inp.llm_calls,
        max_llm_calls=inp.max_llm_calls,
    )


def executed_block(steps: Sequence[StepResult], registry: Registry, k: int) -> str:
    if not steps:
        return prompts.NOTHING_YET
    lines: list[str] = []
    if len(steps) > k:
        lines.append(prompts.OMITTED_ENTRIES.format(n=len(steps) - k))
        steps = steps[-k:]
    lines.extend(render_step(sr, registry, numbered=True) for sr in steps)
    return "\n".join(lines)


def remaining_block(remaining: Sequence[tuple[int, PlanStep]], tag: RemainingTag | None, registry: Registry) -> str:
    if not remaining or tag is None:
        return prompts.NONE
    return "\n".join(render_remaining(n, step, registry, tag) for n, step in remaining)


def build_user_message(inp: ContextInput, registry: Registry) -> str:
    """The single user message (dispatcher/docs/loop-and-context.md).

    Sections in fixed order, one blank line between.
    """
    sections = [
        _section(prompts.SECTION_PREVIOUS_TASK, previous_task_block(inp.previous, registry)),
        _section(prompts.SECTION_ROBOT, prompts.POSTURE_TEMPLATE.format(posture=inp.posture)),
        _section(prompts.SECTION_TASK, inp.task),
        _section(prompts.SECTION_BUDGET, budget_block(inp)),
        _section(prompts.SECTION_EXECUTED, executed_block(inp.steps, registry, inp.history_k)),
        _section(
            prompts.SECTION_REMAINING, remaining_block(inp.feedback.remaining, inp.feedback.remaining_tag, registry)
        ),
        _section(prompts.SECTION_NOTICE, inp.feedback.notice(inp.max_failures)),
    ]
    return prompts.SECTION_SEPARATOR.join(sections)


def schema_retry_message(user: str, errors: list[str]) -> str:
    """Original user message, byte-identical, + ``"\\n\\n"`` + the Rejection section (dispatcher/docs/llm.md)."""
    return user + prompts.SECTION_SEPARATOR + prompts.rejection_section(errors)
