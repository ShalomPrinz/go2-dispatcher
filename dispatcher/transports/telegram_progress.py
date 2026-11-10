"""Live task progress for the Telegram bot: a ``TaskObserver`` whose lines one message shows, edited in place
(docs/running.md). Records only update the text; the bot's event loop sends the edits, throttled."""

from __future__ import annotations

import asyncio
import math
import sys
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import prompts
from ..context import format_call, render_step

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

__all__ = ["TelegramProgress", "Edit", "EDIT_INTERVAL_S", "PROGRESS_MAX_CHARS", "EDIT_FAILED_WARNING", "robot_line"]

EDIT_INTERVAL_S = 1.0  # min gap between edits; Telegram allows about 1 message/s per chat (tunable)
PROGRESS_MAX_CHARS = 4000  # Telegram's limit is 4096; the oldest lines are dropped beyond this
EDIT_FAILED_WARNING = "Warning: Telegram progress update failed: {exception_type}."

Edit = Callable[[str], Awaitable[Any]]  # sets the progress message's text, e.g. ``Message.edit_text``


def _secs(ms: float) -> float:
    return ms / 1000.0


def _metres(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}"


def robot_line(link: Link, state: RobotState | None, age_s: float | None) -> str:
    """The live robot line: link, posture, speed and turn rate, nearest obstacle per direction, sample age
    (docs/running.md)."""
    head = "Robot: link up" if link == "up" else "Robot: LINK DOWN"
    if state is None:
        return f"{head} | no state yet"
    parts = [head, state.posture]
    v = state.velocity
    speed = "speed n/a" if not v or len(v) < 2 else f"{math.hypot(v[0], v[1]):.2f} m/s"
    turn = "" if state.yaw_speed is None else f", turn {math.degrees(state.yaw_speed):.0f} deg/s"
    parts.append(speed + turn)
    o = state.obstacle_m
    if o is None:
        parts.append("obstacles n/a")
    else:
        parts.append(f"obstacles m F {_metres(o.front)} B {_metres(o.back)} L {_metres(o.left)} R {_metres(o.right)}")
    if age_s is not None:
        parts.append(f"{age_s:.1f} s ago")
    return " | ".join(parts)


@dataclass
class _Session:
    """One task's progress message: woken from any thread through its event loop."""

    loop: asyncio.AbstractEventLoop
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    pump: asyncio.Task[None] | None = None


@dataclass
class _Robot:
    """The latest state stream event of the shown task; ``link`` is None until the stream starts."""

    link: Link | None = None
    state: RobotState | None = None
    rx_mono: float | None = None  # arrival of ``state`` in the service, time.monotonic()
    woken: bool = False
    ended: bool = False


class TelegramProgress:
    """Observer methods (loop thread, or the event loop for a stop) only change the lines under a lock and wake the
    pump, so they never wait on Telegram. The pump runs on the bot's event loop and edits the message at most once
    per ``interval_s``; a failed edit is printed to stderr and skipped."""

    def __init__(
        self, registry: Registry, *, interval_s: float = EDIT_INTERVAL_S, clock: Callable[[], float] = time.monotonic
    ):
        self._registry = registry
        self._interval_s = interval_s
        self._clock = clock
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._pending: str | None = None  # "Thinking..." or the running step; the next line replaces it
        self._session: _Session | None = None
        self._robot = _Robot()

    # --- bot side (event loop) --------------------------------------------------------------------

    def open(self, edit: Edit) -> bool:
        """Start showing a task in the message ``edit`` changes; False if a task is already shown."""
        if self._session is not None:
            return False
        session = _Session(asyncio.get_running_loop())
        with self._lock:
            self._lines, self._pending = [], None
            self._robot = _Robot()
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
            robot = self._robot_line()
        if robot is None and not lines:
            return prompts.WORKING
        lines = lines or [prompts.WORKING]
        tail = [robot] if robot is not None else []  # the robot line is never dropped
        text = "\n".join(lines + tail)
        dropped = 0
        while len(text) > PROGRESS_MAX_CHARS and dropped < len(lines) - 1:
            dropped += 1
            text = "\n".join([prompts.OMITTED_LINES.format(n=dropped)] + lines[dropped:] + tail)
        return text

    def _robot_line(self) -> str | None:
        """The robot line while the stream is live, else None; call with the lock held."""
        r = self._robot
        if r.link is None:
            return None
        age = None if r.rx_mono is None or r.state is None else max(0.0, self._clock() - r.rx_mono)
        return robot_line(r.link, r.state, age)

    async def _pump(self, edit: Edit, session: _Session) -> None:
        shown = prompts.WORKING  # the text the message was sent with
        while True:
            if not self._robot_live():  # a live robot line refreshes its age every interval
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

    def _robot_live(self) -> bool:
        with self._lock:
            return self._robot.link is not None

    # --- observer side (any thread) ---------------------------------------------------------------

    def _update(self, *, add: list[str] | None = None, pending: str | None = None) -> None:
        """Append ``add``, replace the pending line with ``pending``, and wake the pump."""
        with self._lock:
            session = self._session
            if session is None:
                return
            self._lines += add or []
            self._pending = pending
        self._wake(session)

    def _wake(self, session: _Session) -> None:
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
        with self._lock:
            self._robot = _Robot(ended=True)  # the robot line leaves the final text; later samples are dropped
        self._update()  # the outcome goes out as a new message

    def robot_sample(self, state: RobotState, link: Link, rx_mono: float | None) -> None:
        self._robot_event(link, state, rx_mono)

    def robot_link(self, link: Link, rx_mono: float | None) -> None:
        self._robot_event(link, None, rx_mono)

    def _robot_event(self, link: Link, state: RobotState | None, rx_mono: float | None) -> None:
        """Keep only the latest link and sample (stream thread); the pump shows them at its next edit."""
        with self._lock:
            session = self._session
            if session is None or self._robot.ended:
                return
            self._robot.link = link
            if state is not None:
                self._robot.state, self._robot.rx_mono = state, rx_mono
            first = not self._robot.woken
            self._robot.woken = True
        if first:  # later events need no wake: the pump ticks every interval while the line is live
            self._wake(session)
