"""JSONL run log: one file per task plus an index (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from pydantic import BaseModel

if TYPE_CHECKING:
    from skills.result import MotionCost

    from .budget import MotionBudget
    from .config import Config
    from .context import PromptSurface
    from .llm import LLMResult
    from .models import Plan, StepResult, StopMoveResult, TaskOutcome, TaskSummary

INDEX_FILE = "index.jsonl"
FILE_TIME_FORMAT = "%Y%m%dT%H%M%S"  # local time at task start (dispatcher/docs/run-log.md)
RUN_ID_PREFIX_LEN = 8
ENVELOPE_KEYS = frozenset({"ts", "t_mono_ms", "session_id", "run_id", "seq", "type"})
GIT_TIMEOUT_S = 5.0  # best-effort `git rev-parse HEAD` at startup
VERSION_PACKAGES = ("anthropic", "pydantic", "go2-dispatcher")


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in VERSION_PACKAGES:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def _git_commit(base_dir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=base_dir, capture_output=True, text=True, timeout=GIT_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


class SessionInfo(BaseModel):
    """The ``task_start`` fields that are constant for the process, collected once at startup."""

    condition: str
    config: dict[str, Any]
    registry_hash: str
    system_text: str
    catalog_text: str
    tool_schema: dict[str, Any]
    skills: list[str]
    versions: dict[str, str | None]
    git_commit: str | None

    @classmethod
    def collect(cls, cfg: Config, surface: PromptSurface) -> SessionInfo:
        return cls(
            condition=cfg.run.condition,
            config=cfg.model_dump(mode="json"),
            registry_hash=surface.registry_hash,
            system_text=surface.system[0],
            catalog_text=surface.catalog_text,
            tool_schema=surface.tool_schema,
            skills=list(surface.skills),
            versions=_versions(),
            git_commit=_git_commit(cfg.base_dir),
        )


class RunLog:
    """One task's log file, one method per record type. Writing is thread-safe (``stop_requested``
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

    # --- records (dispatcher/docs/run-log.md) -------------------------------------------------

    def task_start(
        self, task: str, source: str, sender_id: str | None, previous: TaskSummary | None, posture: str
    ) -> None:
        s = self._session
        self._write(
            "task_start",
            task=task,
            source=source,
            sender_id=sender_id,
            condition=s.condition,
            config=s.config,
            registry_hash=s.registry_hash,
            system_text=s.system_text,
            catalog_text=s.catalog_text,
            tool_schema=s.tool_schema,
            skills=s.skills,
            previous_task=previous.model_dump(mode="json") if previous else None,
            posture=posture,
            versions=s.versions,
            git_commit=s.git_commit,
        )

    def llm_request(self, call_index: int, return_reason: str, retry_of: str | None, user_text: str) -> None:
        self._write(
            "llm_request", call_index=call_index, return_reason=return_reason, retry_of=retry_of, user_text=user_text
        )

    def llm_retry(self, retry: dict) -> None:
        self._write("llm_retry", **retry)

    def llm_response(self, call_index: int, res: LLMResult) -> None:
        self._write(
            "llm_response",
            call_index=call_index,
            latency_ms=res.latency_ms,
            total_ms=res.total_ms,
            attempts=res.attempts,
            stop_reason=res.stop_reason,
            usage=res.usage,
            content=res.content,
            response_id=res.response_id,
            request_id=res.request_id,
        )

    def llm_error(self, call_index: int, detail: str) -> None:
        self._write("llm_error", call_index=call_index, detail=detail)

    def llm_interrupted(self, call_index: int, cause: str) -> None:
        self._write("llm_interrupted", call_index=call_index, cause=cause)

    def plan(self, call_index: int, plan: Plan, stop_at: int) -> None:
        self._write("plan", call_index=call_index, plan=plan.model_dump(mode="json"), stop_at=stop_at)

    def plan_invalid(self, call_index: int, res: LLMResult, horizon: int) -> None:
        is_horizon = res.rejection_kind == "horizon"
        self._write(
            "plan_invalid",
            call_index=call_index,
            tool_input=res.tool_input,
            rejection_kind=res.rejection_kind,
            steps_in_plan=len(res.tool_input["steps"]) if is_horizon else None,  # horizon implies a steps list
            horizon=horizon if is_horizon else None,
            errors=res.errors,
        )

    def step_start(
        self,
        *,
        index: int,
        call_index: int,
        plan_step: int,
        skill: str,
        params: dict,
        timeout_s: float,
        cost: MotionCost,
        fault: str | None,
    ) -> None:
        self._write(
            "step_start",
            index=index,
            call_index=call_index,
            plan_step=plan_step,
            skill=skill,
            params=params,
            timeout_s=timeout_s,
            motion_cost=asdict(cost),
            fault=fault,
        )

    def step_result(self, sr: StepResult, *, budget: MotionBudget, failures: int, posture: str) -> None:
        self._write(
            "step_result", **sr.model_dump(mode="json"), budget_used=budget.used(), failures=failures, posture=posture
        )

    def stop_requested(self, source: str, during: str) -> None:
        self._write("stop_requested", source=source, during=during)

    def stop_move(self, smr: StopMoveResult) -> None:
        self._write("stop_move", **smr.model_dump(mode="json"))

    def exception(self, where: str, e: BaseException) -> None:
        self._write(
            "exception",
            where=where,
            exception_type=type(e).__name__,
            message=str(e),
            traceback="".join(traceback.format_exception(type(e), e, e.__traceback__)),
        )

    def task_end(
        self, result: TaskOutcome, *, rejections: int, horizon_rejections: int, usage_totals: dict, budget: MotionBudget
    ) -> None:
        self._write(
            "task_end",
            outcome=result.outcome,
            message=result.message,
            llm_calls=result.llm_calls,
            failures=result.failures,
            steps_recorded=len(result.steps),
            steps_dispatched=_dispatched(result),
            rejections=rejections,
            horizon_rejections=horizon_rejections,
            usage_totals=usage_totals,
            budget_used=budget.used(),
            stop_move_failed=result.stop_move_failed,
            final_posture=result.final_posture,
            duration_ms=result.duration_ms,
        )


class NullLog(RunLog):
    """Stands in until the task's log file is open (or if opening it failed): every record is dropped."""

    def __init__(self) -> None:
        self.path = ""  # TaskOutcome.log_path stays empty
        self._lock = threading.Lock()
        self._f = None

    def task_start(self, *args: Any, **kwargs: Any) -> None:
        pass  # the only record that reads the session info


def _dispatched(result: TaskOutcome) -> int:
    return sum(1 for s in result.steps if s.index is not None)


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

    def append_index(self, log: RunLog, result: TaskOutcome, *, ts_start: str, source: str, usage_totals: dict) -> None:
        """One ``index.jsonl`` row for a finished task (dispatcher/docs/run-log.md)."""
        cfg = self.session.config
        row = {
            "run_id": result.run_id,
            "file": log.path.name,
            "ts_start": ts_start,
            "task": result.task,
            "source": source,
            "outcome": result.outcome,
            "condition": self.session.condition,
            "backend": cfg["robot"]["backend"],
            "model": cfg["llm"]["model"],
            "thinking": cfg["llm"]["thinking"],
            "planning_horizon": cfg["loop"]["planning_horizon"],
            "max_llm_calls": cfg["loop"]["max_llm_calls"],
            "registry_hash": self.session.registry_hash,
            "llm_calls": result.llm_calls,
            "failures": result.failures,
            "steps_dispatched": _dispatched(result),
            "input_tokens": usage_totals.get("input_tokens", 0),
            "output_tokens": usage_totals.get("output_tokens", 0),
            "duration_ms": result.duration_ms,
        }
        with self._index_lock:
            with (self.log_dir / INDEX_FILE).open("a", encoding="utf-8") as f:
                f.write(_dumps(row) + "\n")
                f.flush()
