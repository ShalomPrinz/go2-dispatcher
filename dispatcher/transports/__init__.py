"""Transports (CLI, Telegram) over the dispatcher: shared setup and output (docs/running.md)."""

from __future__ import annotations

import atexit
import importlib
import os
import sys
import threading
import uuid
from collections.abc import Callable

from skills import stub

from .. import process_lock
from ..config import Config, load_config_and_env
from ..context import render_step
from ..dispatcher import Dispatcher
from ..executor import Executor
from ..llm import AnthropicPlanner, LLMResult, PlannerClient
from ..models import LLMUnavailable, RegistryError, TaskOutcome
from ..registry import Registry
from ..runlog import RunLogFactory

__all__ = [
    "load_config_and_env",
    "build_dispatcher",
    "format_outcome",
    "make_planner",
    "initial_posture",
    "API_KEY_ENV",
    "TEST_PLANNER_ENV",
    "OUTCOME_MAX_CHARS",
    "OMITTED_LINES",
]

API_KEY_ENV = "ANTHROPIC_API_KEY"
TEST_PLANNER_ENV = "GO2_TEST_PLANNER"  # "module:factory" (tests only)
MISSING_API_KEY = f"Missing {API_KEY_ENV}."
OUTCOME_MAX_CHARS = 4000  # Telegram's limit is 4096 (docs/running.md)
OMITTED_LINES = "({n} earlier lines omitted)"
NO_PLANNER_DETAIL = "no planner configured"


def _exit2(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(2)


class _NoPlanner:
    """Planner for ``need_llm=False``: every call ends the task as LLM_ERROR."""

    def plan(
        self,
        *,
        system: list[str],
        user: str,
        tool_schema: dict,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult:
        raise LLMUnavailable(NO_PLANNER_DETAIL)


def make_planner(cfg: Config, *, need_llm: bool) -> PlannerClient:
    """Choose the planner: test planner, Anthropic, or none (docs/running.md)."""
    spec = os.environ.get(TEST_PLANNER_ENV)
    if spec:
        module_name, sep, attr = spec.partition(":")
        if not sep or not module_name or not attr:
            _exit2(f"{TEST_PLANNER_ENV} must be module:factory, got {spec!r}.")
        factory = getattr(importlib.import_module(module_name), attr)
        return factory()
    if need_llm:
        key = os.environ.get(API_KEY_ENV, "").strip()
        if not key:
            _exit2(MISSING_API_KEY)
        return AnthropicPlanner(key, cfg.llm, cfg.loop.planning_horizon)
    return _NoPlanner()


def initial_posture(cfg: Config, executor: Executor, *, reset: bool) -> str:
    """Posture at start-up: from the stub state file or a robot state read (docs/running.md)."""
    if cfg.robot.backend == "stub":
        if reset:
            return cfg.stub.initial_posture
        try:
            return stub.read_posture(cfg.stub.state_file)
        except (OSError, ValueError) as e:
            print(f"Warning: cannot read stub state ({e}); posture unknown.", file=sys.stderr)
            return "unknown"
    state = executor.read_state()
    if state is None:
        print("Warning: robot state unavailable; posture unknown.", file=sys.stderr)
        return "unknown"
    return state.posture


def build_dispatcher(
    cfg: Config, *, need_llm: bool, reset_stub: bool, planner: PlannerClient | None = None
) -> Dispatcher:
    """Lock, registry, stub reset, planner, executor, run log factory (docs/running.md)."""
    process_lock.acquire(cfg.log.dir)
    try:
        registry = Registry.load(cfg.skills.dir)
    except RegistryError as e:
        _exit2(f"Registry error: {e}")
    if reset_stub and cfg.robot.backend == "stub":
        stub.write_posture(cfg.stub.initial_posture, cfg.stub.state_file)
    if planner is None:
        planner = make_planner(cfg, need_llm=need_llm)
    executor = Executor(cfg, cfg.base_dir)
    posture = initial_posture(cfg, executor, reset=reset_stub)
    dispatcher = Dispatcher(
        cfg,
        registry,
        planner,
        executor,
        RunLogFactory(cfg.log.dir, session_id=uuid.uuid4().hex),
        initial_posture=posture,
    )
    atexit.register(dispatcher.shutdown, 0)
    return dispatcher


def format_outcome(outcome: TaskOutcome, registry: Registry) -> str:
    """Plain-text outcome for the operator (docs/running.md); oldest step lines are dropped if the
    whole text would exceed ``OUTCOME_MAX_CHARS``."""
    dispatched = sum(1 for s in outcome.steps if s.index is not None)
    head = [f"{outcome.outcome}: {outcome.message}", f"Steps: {dispatched} run, {outcome.failures} failed"]
    steps = [render_step(s, registry, numbered=True) for s in outcome.steps]
    text = "\n".join(head + steps)
    dropped = 0
    while len(text) > OUTCOME_MAX_CHARS and dropped < len(steps):
        dropped += 1
        text = "\n".join(head + [OMITTED_LINES.format(n=dropped)] + steps[dropped:])
    return text
