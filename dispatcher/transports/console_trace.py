"""Live, colored trace of a running task for the CLI: a ``TaskObserver``, never part of the loop (docs/running.md)."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TextIO

from ..context import format_call, render_step

if TYPE_CHECKING:
    from ..budget import MotionBudget
    from ..context import RequestReason, ReturnReason
    from ..llm import LLMResult, PlanCheck
    from ..models import Plan, Posture, StepDispatch, StepRef, StepResult, StopMoveResult, TaskOutcome, TaskSummary
    from ..registry import Registry

NO_COLOR_ENV = "NO_COLOR"  # https://no-color.org
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
CLEAR_LINE = "\r\x1b[2K"
TOKEN_KEY_PAIRS = (("input_tokens", "output_tokens"), ("prompt_tokens", "completion_tokens"))  # Anthropic, OpenRouter


def ansi_enabled(stream: TextIO) -> bool:
    """Color and in-place lines only on a terminal and without ``NO_COLOR``."""
    if os.environ.get(NO_COLOR_ENV):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def _secs(ms: float) -> str:
    return f"{ms / 1000.0:.1f}s"


def _tokens(usage: dict) -> str | None:
    for in_key, out_key in TOKEN_KEY_PAIRS:
        if isinstance(usage.get(in_key), int) and isinstance(usage.get(out_key), int):
            return f"{usage[in_key]} in / {usage[out_key]} out tokens"
    return None


class ConsoleTrace:
    """Writes one line per event to ``stream`` (stderr by default). With ANSI on,
    the "Thinking..." and running-step lines are pending: the next line replaces them in place."""

    def __init__(
        self,
        registry: Registry,
        stream: TextIO | None = None,
        *,
        ansi: bool | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._registry = registry
        self._stream = stream if stream is not None else sys.stderr
        self._ansi = ansi_enabled(self._stream) if ansi is None else ansi
        self._clock = clock
        self._lock = threading.Lock()  # stop_requested arrives from the signal-handling thread
        self._pending: str | None = None
        self._t_request = 0.0
        self._shown_message: str | None = None  # the message the last plan line printed

    # --- output ---------------------------------------------------------------------------

    def _style(self, text: str, *codes: str) -> str:
        return f"{''.join(codes)}{text}{RESET}" if self._ansi and codes else text

    def _out(self, text: str) -> None:
        self._stream.write(text)
        self._stream.flush()

    def _line(self, text: str) -> None:
        """A line of its own; an open line is redrawn below it."""
        with self._lock:
            if self._pending is None:
                self._out(text + "\n")
            else:
                self._out(CLEAR_LINE + text + "\n" + self._pending)

    def _open(self, text: str) -> None:
        """A line the next ``_close`` replaces in place (ANSI only; otherwise a plain line)."""
        with self._lock:
            if not self._ansi:
                self._out(text + "\n")
                return
            self._out((CLEAR_LINE if self._pending is not None else "") + text)
            self._pending = text

    def _close(self, text: str) -> None:
        """A line that replaces the open one, if any."""
        with self._lock:
            self._out((CLEAR_LINE if self._pending is not None else "") + text + "\n")
            self._pending = None

    # --- TaskObserver ---------------------------------------------------------------------

    def task_start(
        self, task: str, source: str, sender_id: str | None, previous: TaskSummary | None, posture: Posture
    ) -> None:
        self._line(self._style(f"Posture: {posture}", DIM))

    def llm_request(
        self, call_index: int, return_reason: RequestReason, retry_of: ReturnReason | None, user_text: str
    ) -> None:
        self._t_request = self._clock()
        why = "" if return_reason == "initial" else self._style(f" ({return_reason})", DIM)
        self._open(self._style("Thinking...", CYAN) + why)

    def llm_retry(self, retry: dict[str, Any]) -> None:
        status = f" {retry['status_code']}" if retry.get("status_code") else ""
        text = f"LLM retry {retry.get('attempt')}: {retry.get('error_type')}{status}, waiting {retry.get('sleep_s')}s"
        self._line(self._style(text, YELLOW))

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        parts = [f"Thought for {self._clock() - self._t_request:.1f}s"]
        tokens = _tokens(res.usage)
        if tokens:
            parts.append(tokens)
        if res.attempts > 1:
            parts.append(f"{res.attempts} attempts")
        self._close(self._style(" · ".join(parts), DIM))

    def llm_error(self, call_index: int, detail: str) -> None:
        self._close(self._style(f"✗ LLM error: {detail}", RED))

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        self._close(self._style(f"LLM call interrupted: {cause}", YELLOW))

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        head = f"Plan: {plan.status}"
        if plan.steps:
            head += f", {len(plan.steps)} step{'s' if len(plan.steps) != 1 else ''}"
            if stop_at < len(plan.steps):
                head += f", replan after {stop_at}"
        if plan.message:
            head += f": {plan.message}"
        self._shown_message = plan.message
        lines = [self._style(head, BOLD)]
        lines += [f"  {i}. {format_call(s.skill, s.params, self._registry)}" for i, s in enumerate(plan.steps, 1)]
        self._line("\n".join(lines))

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None:
        first = check.errors[0] if check.errors else ""
        more = f" (+{len(check.errors) - 1} more)" if len(check.errors) > 1 else ""
        self._line(self._style(f"Plan rejected ({check.rejection_kind}): {first}{more}", YELLOW))

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None:
        self._open(f"▶ {format_call(ref.skill, ref.params, self._registry)}")

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None:
        mark = self._style("✓", GREEN) if sr.outcome == "ok" else self._style("✗", RED)
        line = f"{mark} {render_step(sr, self._registry, numbered=False)}"
        if sr.dispatch is not None:
            line += self._style(f" ({_secs(sr.duration_ms)})", DIM)
        self._close(line)

    def stop_requested(self, source: str, during: str) -> None:
        self._close(
            self._style(f"■ Stop requested ({source}, during {during})", YELLOW)
        )  # the CLI prints "Stopping." next

    def stop_move(self, smr: StopMoveResult) -> None:
        state = "sent" if smr.ok else "FAILED"
        self._close(self._style(f"■ StopMove {state} ({smr.reason.value}, {_secs(smr.duration_ms)})", RED))

    def exception(self, where: str, e: BaseException) -> None:
        self._close(self._style(f"✗ Internal error in {where}: {type(e).__name__}: {e}", RED))

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        parts = [
            result.outcome,
            f"{result.llm_calls} LLM call{'s' if result.llm_calls != 1 else ''}",
            f"{len(result.dispatched_steps)} steps run, {result.failures} failed",
        ]
        tokens = _tokens(usage_totals)
        if tokens:
            parts.append(tokens)
        parts.append(_secs(result.duration_ms))
        color = GREEN if result.outcome == "DONE" else RED
        summary = self._style(" · ".join(parts), BOLD, color)
        if result.message and result.message != self._shown_message:  # e.g. STOPPED, budget, LLM_INVALID
            summary = self._style(result.message, BOLD, color) + "\n" + summary
        self._shown_message = None
        self._close(summary)
