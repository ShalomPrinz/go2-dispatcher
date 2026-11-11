"""Telegram operator text as HTML (parse_mode HTML): escaping, step lines, trimming and the outcome text
(docs/running.md). Shows categories only; error details stay in the run log."""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from .. import prompts
from ..context import NOT_DISPATCHED_OUTCOMES, format_call, render_step
from ..models import FAILURE_OUTCOMES

if TYPE_CHECKING:
    from ..models import StepResult, TaskOutcome
    from ..registry import Registry

__all__ = ["MAX_CHARS", "PART_MAX_CHARS", "h", "call_html", "step_reason", "step_line", "trim", "format_outcome"]

MAX_CHARS = 4000  # Telegram's limit is 4096; the oldest lines are dropped beyond this
PART_MAX_CHARS = 500  # one dynamic part (model text, skill call) before escaping, so one line always fits


def h(text: str) -> str:
    """Escape dynamic content for parse_mode HTML, cut to ``PART_MAX_CHARS`` first."""
    if len(text) > PART_MAX_CHARS:
        text = text[: PART_MAX_CHARS - 1] + "…"
    return html.escape(text, quote=False)  # text only, never inside an attribute


def call_html(skill: str, params: dict, registry: Registry) -> str:
    return h(format_call(skill, params, registry))


def step_reason(sr: StepResult) -> str:
    """Short operator reason for a failed step: from its outcome, or its error code for ``error``."""
    key = (sr.error_code or "") if sr.outcome == "error" else sr.outcome
    if key in prompts.STEP_REASONS:
        return prompts.STEP_REASONS[key]
    return h(prompts.STEP_REASON_FALLBACK.format(code=key.replace("_", " ") or "error"))


def step_line(sr: StepResult, registry: Registry) -> str:
    """``✅ call -> ok: observations`` or ``❌ call reason``, without number or duration."""
    if sr.outcome == "ok":
        return prompts.PROGRESS_STEP_OK.format(step=h(render_step(sr, registry, numbered=False)))
    return prompts.PROGRESS_STEP_FAILED.format(
        call=call_html(sr.ref.skill, sr.ref.params, registry), reason=step_reason(sr)
    )


def trim(head: list[str], lines: list[str], tail: list[str]) -> str:
    """Join ``head + lines + tail``; beyond ``MAX_CHARS`` replace the oldest of ``lines`` (keeping at least one)
    by ``OMITTED_LINES``. Every line is whole markup, so dropping lines never cuts a tag."""
    text = "\n".join(head + lines + tail)
    dropped = 0
    while len(text) > MAX_CHARS and dropped < len(lines) - 1:
        dropped += 1
        text = "\n".join(head + [prompts.OMITTED_LINES.format(n=dropped)] + lines[dropped:] + tail)
    return text


def _last_failure(outcome: TaskOutcome) -> StepResult | None:
    return next((s for s in reversed(outcome.steps) if s.outcome in FAILURE_OUTCOMES), None)


def format_outcome(outcome: TaskOutcome, registry: Registry) -> str:
    """The reply that ends a task (docs/running.md): outcome line, step counts and one line per recorded step,
    then the last failure and the StopMove warning."""
    code = outcome.outcome
    n = outcome.llm_calls if code == "CALL_BUDGET_EXHAUSTED" else outcome.failures
    first = prompts.TELEGRAM_OUTCOMES[code].format(message=h(outcome.message), n=n, run_id=h(outcome.run_id))
    last = _last_failure(outcome) if code == "FAILURE_BUDGET_EXHAUSTED" else None
    tail = []
    if last is not None:
        tail.append(prompts.TELEGRAM_LAST_FAILURE.format(skill=h(last.ref.skill), reason=step_reason(last)))
    if outcome.stop_move_failed:
        tail.append(prompts.TELEGRAM_STOP_MOVE_WARNING)
    head = [first, "", prompts.STEPS_LINE.format(run=len(outcome.dispatched_steps), failed=outcome.failures)]
    steps = [_numbered(s, registry) for s in outcome.steps]
    return trim(head, steps, [""] + tail if tail else [])  # blank lines around the counts and list


def _numbered(sr: StepResult, registry: Registry) -> str:
    """``{index}. {line}`` for a dispatched step, ``– {line}`` for one rejected before running."""
    d = sr.dispatch
    prefix = "–" if d is None or sr.outcome in NOT_DISPATCHED_OUTCOMES else f"{d.index}."
    return f"{prefix} {step_line(sr, registry)}"
