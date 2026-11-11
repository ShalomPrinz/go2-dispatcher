"""Live task progress for the Telegram bot: a ``TaskObserver`` whose lines one message per model call shows, edited
in place (docs/running.md). Records only update the text; the bot's event loop sends, throttled."""

from __future__ import annotations

import asyncio
import math
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import prompts
from .telegram_text import call_html, h, step_line, trim

if TYPE_CHECKING:
    from skills.state_store import Link

    from ..budget import MotionBudget
    from ..context import RequestReason, ReturnReason
    from ..llm import LLMResult, PlanCheck
    from ..models import (
        Plan,
        Posture,
        RobotState,
        StartState,
        StepDispatch,
        StepRef,
        StepResult,
        StopMoveResult,
        TaskOutcome,
        TaskSummary,
    )
    from ..registry import Registry

__all__ = ["TelegramProgress", "Edit", "NewMessage", "EDIT_INTERVAL_S", "EDIT_FAILED_WARNING", "robot_line"]

EDIT_INTERVAL_S = 1.0  # min gap between Telegram calls; Telegram allows about 1 message/s per chat (tunable)
EDIT_FAILED_WARNING = "Warning: Telegram progress update failed: {exception_type}."
ROBOT_LINK_UP = "🟢 Robot: link up"
ROBOT_LINK_DOWN = "🔴 Robot: link down"

Edit = Callable[[str], Awaitable[Any]]  # sets a progress message's HTML text, e.g. ``Message.edit_text``
NewMessage = Callable[[str], Awaitable[Edit]]  # sends a new progress message with this HTML text; returns its Edit


def _secs(ms: float) -> float:
    return ms / 1000.0


def _metres(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def robot_line(link: Link, state: RobotState | None) -> str:
    """The live robot line as HTML: link, posture, speed and turn rate, nearest obstacle per direction
    (docs/running.md)."""
    head = ROBOT_LINK_UP if link == "up" else ROBOT_LINK_DOWN
    if state is None:
        return f"{head} | no state yet"
    parts = [head, h(state.posture)]
    v = state.velocity
    speed = "speed n/a" if not v or len(v) < 2 else f"{math.hypot(v[0], v[1]):.2f} m/s"
    turn = "" if state.yaw_speed is None else f", turn {math.degrees(state.yaw_speed):.0f} deg/s"
    parts.append(speed + turn)
    o = state.obstacle_m
    if o is None:
        parts.append("obstacles n/a")
    else:
        parts.append(f"obstacles m F {_metres(o.front)} B {_metres(o.back)} L {_metres(o.left)} R {_metres(o.right)}")
    return " | ".join(parts)


@dataclass
class _Round:
    """One model call's lines: its reply, the plan and the steps it ran; each round has its own message."""

    lines: list[str] = field(default_factory=list)
    plan_end: int | None = None  # index after the plan's step list; a blank line goes there once more follows
    pending: str | None = None  # "Thinking..." or the running step; the next line replaces it


@dataclass
class _Session:
    """One task's progress messages: woken from any thread through its event loop."""

    loop: asyncio.AbstractEventLoop
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    pump: asyncio.Task[None] | None = None


@dataclass
class _Robot:
    """The shown task's latest robot line and sample; ``line`` is None until the stream starts."""

    line: str | None = None
    state: RobotState | None = None
    ended: bool = False


class TelegramProgress:
    """Observer methods (loop thread, or the event loop for a stop) only change the lines under a lock and wake the
    pump, so they never wait on Telegram. The pump runs on the bot's event loop, makes at most one Telegram call per
    ``interval_s`` and only when a message's text changed; a failed call is printed to stderr and retried at the
    next wake."""

    def __init__(self, registry: Registry, *, interval_s: float = EDIT_INTERVAL_S):
        self._registry = registry
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._rounds: list[_Round] = [_Round()]
        self._calls = 0  # model calls seen in this task
        self._rejected: str | None = None  # a rejected-plan line awaiting "asking again" or "giving up"
        self._session: _Session | None = None
        self._robot = _Robot()

    # --- bot side (event loop) --------------------------------------------------------------------

    def open(self, edit: Edit, new_message: NewMessage) -> bool:
        """Start showing a task: the first round in the message ``edit`` changes, later rounds in messages
        ``new_message`` sends. False if a task is already shown."""
        if self._session is not None:
            return False
        session = _Session(asyncio.get_running_loop())
        with self._lock:
            self._rounds, self._calls, self._rejected = [_Round()], 0, None
            self._robot = _Robot()
            self._session = session
        session.pump = asyncio.create_task(self._pump(edit, new_message, session))
        return True

    async def close(self) -> None:
        """Send the last state of the messages, then stop showing the task."""
        session = self._session
        if session is None:
            return
        session.closed.set()
        session.changed.set()
        if session.pump is not None:
            await session.pump
        with self._lock:
            self._session = None

    def texts(self) -> list[str]:
        """The HTML text of each round's message, oldest first."""
        with self._lock:
            return [self._round_text(i) for i in range(len(self._rounds))]

    def text(self) -> str:
        """The newest round's message text."""
        return self.texts()[-1]

    def _round_text(self, i: int) -> str:
        """Call with the lock held. Only the newest round shows the robot line, which is never dropped."""
        r = self._rounds[i]
        lines = r.lines + ([r.pending] if r.pending is not None else [])
        if r.plan_end is not None and len(lines) > r.plan_end:  # a blank line between the plan and what followed
            lines.insert(r.plan_end, "")
        robot = self._robot.line if i == len(self._rounds) - 1 else None
        if robot is None and not lines:
            return prompts.WORKING
        return trim([], lines or [prompts.WORKING], [robot] if robot is not None else [])

    async def _pump(self, edit: Edit, new_message: NewMessage, session: _Session) -> None:
        shown_round, shown = 0, prompts.WORKING  # the newest message sent and its text after the last success
        next_call = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await session.changed.wait()
            delay = next_call - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)  # events in between are merged into this call
            session.changed.clear()
            closing = session.closed.is_set()  # set only after the task ended, so this snapshot is final
            texts = self.texts()[shown_round:]
            if texts[0] != shown:
                call, text = edit, texts[0]
            elif len(texts) > 1:
                call, text = new_message, texts[1]
            elif closing:
                return
            else:
                continue
            try:
                result = await call(text)
            except Exception as e:  # noqa: BLE001 - progress is best effort (docs/running.md)
                print(EDIT_FAILED_WARNING.format(exception_type=type(e).__name__), file=sys.stderr, flush=True)
                if closing:
                    return
                next_call = loop.time() + self._interval_s
                continue
            if call is new_message:
                edit, shown_round = result, shown_round + 1
            shown = text
            next_call = loop.time() + self._interval_s
            session.changed.set()  # look again: more rounds, or changes during the call

    # --- observer side (any thread) ---------------------------------------------------------------

    def _update(self, *, add: list[str] | None = None, pending: str | None = None) -> None:
        """Append ``add`` to the newest round, replace its pending line with ``pending``, and wake the pump."""
        with self._lock:
            session = self._session
            if session is None:
                return
            r = self._rounds[-1]
            r.lines += add or []
            r.pending = pending
        self._wake(session)

    def _wake(self, session: _Session) -> None:
        try:
            session.loop.call_soon_threadsafe(session.changed.set)
        except RuntimeError:  # the event loop is closed (bot shutdown)
            pass

    def _resolve_rejected(self, then: str) -> None:
        """Append the waiting rejected-plan line, ending in ``then``; call with the lock held."""
        if self._rejected is not None:
            self._rounds[-1].lines.append(self._rejected.format(then=then))
            self._rejected = None

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
        with self._lock:
            self._resolve_rejected(prompts.PROGRESS_ASKING_AGAIN)
            if self._calls > 0:  # every model call after the first starts a new round, in a new message
                self._rounds[-1].pending = None
                self._rounds.append(_Round())
            self._calls += 1
        self._update(pending=prompts.PROGRESS_THINKING)

    def llm_retry(self, retry: dict[str, Any]) -> None:
        pass  # infrastructure retries stay in the run log (docs/running.md)

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        self._update()

    def llm_error(self, call_index: int, detail: str) -> None:
        self._update()  # the detail stays in the run log; the outcome text names the category

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        self._update()

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        if plan.status == "PLAN":
            calls = [call_html(s.skill, s.params, self._registry) for s in plan.steps]
            head = (
                prompts.PROGRESS_PLAN_MESSAGE.format(message=h(plan.message)) if plan.message else prompts.PROGRESS_PLAN
            )
            add = [head, ""] + [prompts.PROGRESS_PLAN_STEP.format(i=i, call=c) for i, c in enumerate(calls, 1)]
            with self._lock:
                r = self._rounds[-1]
                r.plan_end = len(r.lines) + len(add)
        else:
            template = prompts.PROGRESS_PLAN_DONE if plan.status == "DONE" else prompts.PROGRESS_PLAN_ABORT
            add = [template.format(message=h(plan.message or ""))]
        self._update(add=add)

    def plan_invalid(self, call_index: int, tool_input: Any, check: PlanCheck, horizon: int) -> None:
        self._reject(prompts.PROGRESS_PLAN_MALFORMED)

    def _reject(self, template: str) -> None:
        """Hold a rejected-plan line until the next model call or the task end says what follows."""
        with self._lock:
            self._rejected = template

    def step_start(self, ref: StepRef, dispatch: StepDispatch) -> None:
        self._update(
            pending=prompts.PROGRESS_STEP_RUNNING.format(call=call_html(ref.skill, ref.params, self._registry))
        )

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: Posture) -> None:
        if sr.dispatch is None:  # rejected before running: the plan, not the step, failed
            over = sr.outcome == "motion_budget_exceeded"
            self._reject(prompts.PROGRESS_PLAN_OVER_BUDGET if over else prompts.PROGRESS_PLAN_MALFORMED)
            self._update()
            return
        line = step_line(sr, self._registry) + prompts.PROGRESS_DURATION.format(seconds=_secs(sr.duration_ms))
        self._update(add=[line])

    def stop_requested(self, source: str, during: str) -> None:
        self._update(add=[prompts.PROGRESS_STOP_REQUESTED.format(source=h(source))])

    def stop_move(self, smr: StopMoveResult) -> None:
        template = prompts.PROGRESS_STOP_MOVE_SENT if smr.ok else prompts.PROGRESS_STOP_MOVE_FAILED
        self._update(add=[template.format(seconds=_secs(smr.duration_ms))])

    def exception(self, where: str, e: BaseException) -> None:
        pass  # details stay in the run log; the outcome text names the category

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        with self._lock:
            self._resolve_rejected(prompts.PROGRESS_GIVING_UP)
            self._robot = _Robot(ended=True)  # the robot line leaves the final text; later samples are dropped
        self._update()  # the outcome goes out as a new message

    def robot_sample(self, state: RobotState, link: Link, rx_mono: float | None) -> None:
        self._robot_event(link, state)

    def robot_link(self, link: Link, rx_mono: float | None) -> None:
        self._robot_event(link, None)

    def _robot_event(self, link: Link, state: RobotState | None) -> None:
        """Keep the latest link and sample (stream thread); wake the pump only when the rendered line changed."""
        with self._lock:
            session = self._session
            if session is None or self._robot.ended:
                return
            r = self._robot
            r.state = state or r.state  # a link change keeps the last sample
            line = robot_line(link, r.state)
            if line == r.line:
                return
            r.line = line
        self._wake(session)
