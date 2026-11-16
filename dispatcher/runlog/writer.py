"""The task's log file, written by one ``emit`` per event, and the index writer (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from typing_extensions import assert_never

from .events import (
    ExceptionRaised,
    LlmError,
    LlmInterrupted,
    LlmRequest,
    LlmResponse,
    LlmRetry,
    PlanInvalid,
    PlanRecord,
    PreconditionFailed,
    StepRecord,
    StepStart,
    StopMove,
    StopRequested,
    TaskEnd,
    TaskEvent,
    TaskStart,
)
from .format import ENVELOPE_KEYS, FILE_TIME_FORMAT, INDEX_FILE, ROBOT_IO, RUN_ID_PREFIX_LEN, TOKEN_KEYS
from .session import SessionInfo

if TYPE_CHECKING:
    from ..models import TaskOutcome


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class RunLog:
    """One task's log file: ``emit`` writes each event as its record. Writing is thread-safe (``stop_requested``
    comes from the transport thread) and flushes every line, so a crash leaves a usable partial log."""

    def __init__(
        self,
        path: Path,
        *,
        session: SessionInfo,
        session_id: str,
        run_id: str,
        t_start_mono: float,
        clock: Callable[[], float],
    ):
        self.path = Path(path)
        self._session = session
        self._session_id = session_id
        self._run_id = run_id
        self._t0 = t_start_mono
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._f: TextIO | None = self.path.open("a", encoding="utf-8")

    def _write(self, type: str, /, **payload: Any) -> None:
        with self._lock:
            if self._f is None:
                return
            record = {
                "ts": _local_iso(),
                "t_mono_ms": (self._clock() - self._t0) * 1000.0,
                "session_id": self._session_id,
                "run_id": self._run_id,
                "seq": self._seq,
                "type": type,
            }
            clash = ENVELOPE_KEYS.intersection(payload)
            if clash:
                raise ValueError(f"payload keys clash with the record envelope: {sorted(clash)}")
            record.update(payload)
            self._f.write(_dumps(record) + "\n")
            self._f.flush()
            self._seq += 1

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None

    def emit(self, event: TaskEvent) -> None:
        """Write ``event`` as the record of the same name; an unknown event type raises (dispatcher/docs/run-log.md)."""
        match event:
            case TaskStart():
                s = self._session
                self._write(
                    "task_start",
                    task=event.task,
                    source=event.source,
                    sender_id=event.sender_id,
                    condition=s.condition,
                    config=s.config.model_dump(mode="json"),
                    registry_hash=s.registry_hash,
                    system_text=s.system_text,
                    catalog_text=s.catalog_text,
                    tool_schema=s.tool_schema,
                    skills=s.skills,
                    previous_task=event.previous.model_dump(mode="json") if event.previous else None,
                    posture=event.posture,
                    robot_io=ROBOT_IO,
                    state_age_ms=event.start_state.age_ms,
                    state_error=event.start_state.error,
                    versions=s.versions,
                    git_commit=s.git_commit,
                )
            case LlmRequest():
                self._write(
                    "llm_request",
                    call_index=event.call_index,
                    return_reason=event.return_reason,
                    retry_of=event.retry_of,
                    user_text=event.user_text,
                )
            case LlmRetry(retry=retry):
                self._write(
                    "llm_retry",
                    call_index=retry.call_index,
                    attempt=retry.attempt,
                    error_type=retry.error_type,
                    status_code=retry.status_code,
                    attempt_latency_ms=retry.attempt_latency_ms,
                    sleep_s=retry.sleep_s,
                )
            case LlmResponse(result=res):
                self._write(
                    "llm_response",
                    call_index=event.call_index,
                    latency_ms=res.latency_ms,
                    total_ms=res.total_ms,
                    attempts=res.attempts,
                    stop_reason=res.stop_reason,
                    usage=res.usage,
                    content=res.content,
                    response_id=res.response_id,
                    request_id=res.request_id,
                )
            case LlmError():
                self._write("llm_error", call_index=event.call_index, detail=event.detail)
            case LlmInterrupted():
                self._write("llm_interrupted", call_index=event.call_index, cause=event.cause)
            case PlanRecord():
                self._write(
                    "plan", call_index=event.call_index, plan=event.plan.model_dump(mode="json"), stop_at=event.stop_at
                )
            case PlanInvalid(tool_input=tool_input, check=check):
                is_horizon = check.rejection_kind == "horizon"
                steps_in_plan = None
                if is_horizon:
                    assert tool_input is not None  # horizon implies a steps list
                    steps_in_plan = len(tool_input["steps"])
                self._write(
                    "plan_invalid",
                    call_index=event.call_index,
                    tool_input=tool_input,
                    rejection_kind=check.rejection_kind,
                    steps_in_plan=steps_in_plan,
                    horizon=event.horizon if is_horizon else None,
                    errors=check.errors,
                )
            case StepStart():
                self._write(
                    "step_start", ref=event.ref.model_dump(mode="json"), dispatch=event.dispatch.model_dump(mode="json")
                )
            case StepRecord():
                self._write(
                    "step_result",
                    **event.result.model_dump(mode="json"),
                    budget_used=event.budget.used(),
                    failures=event.failures,
                    posture=event.posture,
                )
            case PreconditionFailed():
                self._write("precondition_failed", **event.failure.model_dump(mode="json"))
            case StopRequested():
                self._write("stop_requested", source=event.source, during=event.during)
            case StopMove():
                self._write("stop_move", **event.result.model_dump(mode="json"))
            case ExceptionRaised(error=e):
                self._write(
                    "exception",
                    where=event.where,
                    exception_type=type(e).__name__,
                    message=str(e),
                    traceback="".join(traceback.format_exception(type(e), e, e.__traceback__)),
                )
            case TaskEnd(result=result):
                self._write(
                    "task_end",
                    outcome=result.outcome,
                    message=result.message,
                    llm_calls=result.llm_calls,
                    failures=result.failures,
                    steps_recorded=len(result.steps),
                    steps_dispatched=len(result.dispatched_steps),
                    rejections=event.rejections,
                    horizon_rejections=event.horizon_rejections,
                    usage_totals=event.usage_totals,
                    budget_used=event.budget.used(),
                    stop_move_failed=result.stop_move_failed,
                    final_posture=result.final_posture,
                    duration_ms=result.duration_ms,
                )
            case _:
                assert_never(event)


class RunLogFactory:
    def __init__(self, log_dir: Path, session_id: str, session: SessionInfo):
        self.log_dir = Path(log_dir)
        self.session_id = session_id
        self.session = session
        self._index_lock = threading.Lock()

    def open(self, run_id: str, t_start_mono: float, *, clock: Callable[[], float] = time.monotonic) -> RunLog:
        """``clock`` is the clock ``t_start_mono`` was read from (default: monotonic)."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now().strftime(FILE_TIME_FORMAT)}_{run_id[:RUN_ID_PREFIX_LEN]}.jsonl"
        return RunLog(
            self.log_dir / name,
            session=self.session,
            session_id=self.session_id,
            run_id=run_id,
            t_start_mono=t_start_mono,
            clock=clock,
        )

    def append_index(self, path: Path, result: TaskOutcome, *, ts_start: str, source: str, usage_totals: dict) -> None:
        """One ``index.jsonl`` row for a finished task whose log file is ``path`` (dispatcher/docs/run-log.md)."""
        cfg = self.session.config
        input_key, output_key = TOKEN_KEYS[cfg.llm.provider]
        row = {
            "run_id": result.run_id,
            "file": path.name,
            "ts_start": ts_start,
            "task": result.task,
            "source": source,
            "outcome": result.outcome,
            "condition": self.session.condition,
            "provider": cfg.llm.provider,
            "backend": cfg.robot.backend,
            "robot_io": ROBOT_IO,
            "model": cfg.llm.model,
            "thinking": cfg.llm.thinking if cfg.llm.provider == "anthropic" else None,
            "planning_horizon": cfg.loop.planning_horizon,
            "max_llm_calls": cfg.loop.max_llm_calls,
            "registry_hash": self.session.registry_hash,
            "llm_calls": result.llm_calls,
            "failures": result.failures,
            "steps_dispatched": len(result.dispatched_steps),
            "input_tokens": usage_totals.get(input_key, 0),
            "output_tokens": usage_totals.get(output_key, 0),
            "duration_ms": result.duration_ms,
        }
        with self._index_lock:
            with (self.log_dir / INDEX_FILE).open("a", encoding="utf-8") as f:
                f.write(_dumps(row) + "\n")
                f.flush()
