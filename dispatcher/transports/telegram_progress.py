"""Live task progress for the Telegram bot: a ``TaskObserver`` whose lines one message shows, edited in place
(docs/running.md). Records only update the text; the bot's event loop sends the edits, throttled."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import prompts
from ..context import format_call, render_step

if TYPE_CHECKING:
    from ..budget import MotionBudget
    from ..context import RequestReason, ReturnReason
    from ..llm import LLMResult, PlanCheck
    from ..models import (
        Plan,
        Posture,
        StartState,
        StepDispatch,
        StepRef,
        StepResult,
        StopMoveResult,
        TaskOutcome,
        TaskSummary,
    )
    from ..registry import Registry

__all__ = ["TelegramProgress", "Edit", "EDIT_INTERVAL_S", "PROGRESS_MAX_CHARS", "EDIT_FAILED_WARNING"]

EDIT_INTERVAL_S = 1.0  # min gap between edits; Telegram allows about 1 message/s per chat (tunable)
PROGRESS_MAX_CHARS = 4000  # Telegram's limit is 4096; the oldest lines are dropped beyond this
EDIT_FAILED_WARNING = "Warning: Telegram progress update failed: {exception_type}."

Edit = Callable[[str], Awaitable[Any]]  # sets the progress message's text, e.g. ``Message.edit_text``


def _secs(ms: float) -> float:
    return ms / 1000.0


@dataclass
class _Session:
    """One task's progress message: woken from any thread through its event loop."""

    loop: asyncio.AbstractEventLoop
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    pump: asyncio.Task[None] | None = None


class TelegramProgress:
    """Observer methods (loop thread, or the event loop for a stop) only change the lines under a lock and wake the
    pump, so they never wait on Telegram. The pump runs on the bot's event loop and edits the message at most once
    per ``interval_s``; a failed edit is printed to stderr and skipped."""

    def __init__(self, registry: Registry, *, interval_s: float = EDIT_INTERVAL_S):
        self._registry = registry
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._pending: str | None = None  # "Thinking..." or the running step; the next line replaces it
        self._session: _Session | None = None

    # --- bot side (event loop) --------------------------------------------------------------------

    def open(self, edit: Edit) -> bool:
        """Start showing a task in the message ``edit`` changes; False if a task is already shown."""
        if self._session is not None:
            return False
        session = _Session(asyncio.get_running_loop())
        with self._lock:
            self._lines, self._pending = [], None
            self._session = session
        session.pump = asyncio.create_task(self._pump(edit, session))
        return True

    async def close(self) -> None:
        """Send the last state of the message, then stop showing the task."""
        session = self._session
        if session is None:
            return
        session.closed.set()
        session.changed.set()
        if session.pump is not None:
            await session.pump
        with self._lock:
            self._session = None

    def text(self) -> str:
        with self._lock:
            lines = self._lines + ([self._pending] if self._pending is not None else [])
        if not lines:
            return prompts.WORKING
        text = "\n".join(lines)
        dropped = 0
        while len(text) > PROGRESS_MAX_CHARS and dropped < len(lines) - 1:
            dropped += 1
            text = "\n".join([prompts.OMITTED_LINES.format(n=dropped)] + lines[dropped:])
        return text

    async def _pump(self, edit: Edit, session: _Session) -> None:
        shown = prompts.WORKING  # the text the message was sent with
        while True:
            await session.changed.wait()
            session.changed.clear()
            closing = session.closed.is_set()  # set only after the task ended, so this snapshot is final
            text = self.text()
            if text != shown:
                try:
                    await edit(text)
                    shown = text
                except Exception as e:  # noqa: BLE001 - progress is best effort (docs/running.md)
                    print(EDIT_FAILED_WARNING.format(exception_type=type(e).__name__), file=sys.stderr, flush=True)
            if closing:
                return
            try:
                await asyncio.wait_for(session.closed.wait(), self._interval_s)
            except asyncio.TimeoutError:
                pass

    # --- observer side (any thread) ---------------------------------------------------------------

    def _update(self, *, add: list[str] | None = None, pending: str | None = None) -> None:
        """Append ``add``, replace the pending line with ``pending``, and wake the pump."""
        with self._lock:
            session = self._session
            if session is None:
                return
            self._lines += add or []
            self._pending = pending
        try:
            session.loop.call_soon_threadsafe(session.changed.set)
        except RuntimeError:  # the event loop is closed (bot shutdown)
            pass

    def task_start(
        self,
        task: str,
        source: str,
        sender_id: str | None,
        previous: TaskSummary | None,
        posture: Posture,
        start_state: StartState,
    ) -> None:
        pass

    def llm_request(
        self, call_index: int, return_reason: RequestReason, retry_of: ReturnReason | None, user_text: str
    ) -> None:
        self._update(pending=prompts.PROGRESS_THINKING)

    def llm_retry(self, retry: dict[str, Any]) -> None:
        pass  # infrastructure retries stay in the run log (docs/running.md)

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        self._update()

    def llm_error(self, call_index: int, detail: str) -> None:
        self._update()

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        self._update()

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        if plan.status == "PLAN":
            calls = [format_call(s.skill, s.params, self._registry) for s in plan.steps]
            head = prompts.PROGRESS_PLAN_MESSAGE.format(message=plan.message) if plan.message else prompts.PROGRESS_PLAN
            add = [head] + [prompts.PROGRESS_PLAN_STEP.format(i=i, call=c) for i, c in enumerate(calls, 1)]
        else:
            add = [prompts.PROGRESS_PLAN_END.format(status=plan.status, message=plan.message or "")]
        self._update(add=add)

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None:
        pass  # the model is asked again; "Thinking..." stays

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None:
        call = format_call(ref.skill, ref.params, self._registry)
        self._update(pending=prompts.PROGRESS_STEP_RUNNING.format(call=call))

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None:
        template = prompts.PROGRESS_STEP_OK if sr.outcome == "ok" else prompts.PROGRESS_STEP_FAILED
        line = template.format(step=render_step(sr, self._registry, numbered=False))
        if sr.dispatch is not None:
            line += prompts.PROGRESS_DURATION.format(seconds=_secs(sr.duration_ms))
        self._update(add=[line])

    def stop_requested(self, source: str, during: str) -> None:
        self._update(add=[prompts.PROGRESS_STOP_REQUESTED.format(source=source)])

    def stop_move(self, smr: StopMoveResult) -> None:
        template = prompts.PROGRESS_STOP_MOVE_SENT if smr.ok else prompts.PROGRESS_STOP_MOVE_FAILED
        self._update(add=[template.format(seconds=_secs(smr.duration_ms))])

    def exception(self, where: str, e: BaseException) -> None:
        pass  # details stay in the run log; the outcome message names the error type

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        self._update()  # the outcome goes out as a new message
