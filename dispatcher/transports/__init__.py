"""Transports (CLI, Telegram) over the dispatcher: shared setup and output (docs/running.md)."""

from __future__ import annotations

import importlib
import os
import sys

from skills import stub

from .. import process_lock, prompts
from ..config import Config, load_config_and_env
from ..context import PromptSurface, render_step
from ..dispatcher import Dispatcher
from ..executor import Executor, SkillExecutor
from ..llm import AnthropicPlanner, PlannerClient
from ..models import ConfigError, Posture, RegistryError, StartupError, TaskOutcome
from ..registry import Registry

__all__ = [
    "load_config_and_env",
    "build_dispatcher",
    "startup_error_text",
    "format_outcome",
    "make_planner",
    "initial_posture",
    "API_KEY_ENV",
    "TEST_PLANNER_ENV",
    "OUTCOME_MAX_CHARS",
]

API_KEY_ENV = "ANTHROPIC_API_KEY"
TEST_PLANNER_ENV = "GO2_TEST_PLANNER"  # "module:factory" (tests only)
MISSING_API_KEY = f"Missing {API_KEY_ENV}."
OUTCOME_MAX_CHARS = 4000  # Telegram's limit is 4096 (docs/running.md)


def startup_error_text(e: StartupError) -> str:
    """Stderr line for a start-up error; the entry point prints it and exits 2 (docs/running.md)."""
    if isinstance(e, ConfigError):
        return f"Config error: {e}"
    if isinstance(e, RegistryError):
        return f"Registry error: {e}"
    return str(e)


def make_planner(cfg: Config, surface: PromptSurface) -> PlannerClient:
    """Choose the planner for ``surface``: test planner or Anthropic (docs/running.md).
    Raises ``StartupError`` for a malformed test planner spec or a missing API key."""
    spec = os.environ.get(TEST_PLANNER_ENV)
    if spec:
        module_name, sep, attr = spec.partition(":")
        if not sep or not module_name or not attr:
            raise StartupError(f"{TEST_PLANNER_ENV} must be module:factory, got {spec!r}.")
        factory = getattr(importlib.import_module(module_name), attr)
        return factory(surface)
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise StartupError(MISSING_API_KEY)
    return AnthropicPlanner(key, cfg.llm, surface)


def initial_posture(executor: SkillExecutor) -> Posture:
    """Posture at start-up from a ``read_state`` call on either backend (docs/running.md)."""
    state = executor.read_state()
    if state is None:
        print("Warning: robot state unavailable; posture unknown.", file=sys.stderr)
        return "unknown"
    return state.posture


def build_dispatcher(cfg: Config, *, reset_stub: bool) -> Dispatcher:
    """Lock, registry, stub reset, planner, executor, dispatcher (docs/running.md).
    Raises ``StartupError``; process-lifetime hooks are the caller's."""
    process_lock.acquire(cfg.log.dir)
    registry = Registry.load(cfg.skills.dir)
    if reset_stub and cfg.robot.backend == "stub":
        stub.reset(cfg.stub.initial_posture, cfg.stub.state_file)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    planner = make_planner(cfg, surface)
    executor = Executor(cfg, cfg.base_dir)
    posture = initial_posture(executor)
    return Dispatcher(
        cfg,
        registry,
        planner,
        executor,
        initial_posture=posture,
    )


def format_outcome(outcome: TaskOutcome, registry: Registry) -> str:
    """Plain-text outcome for the operator (docs/running.md); oldest step lines are dropped if the
    whole text would exceed ``OUTCOME_MAX_CHARS``."""
    run = len(outcome.dispatched_steps)
    head = [f"{outcome.outcome}: {outcome.message}", prompts.STEPS_LINE.format(run=run, failed=outcome.failures)]
    steps = [render_step(s, registry, numbered=True) for s in outcome.steps]
    text = "\n".join(head + steps)
    dropped = 0
    while len(text) > OUTCOME_MAX_CHARS and dropped < len(steps):
        dropped += 1
        text = "\n".join(head + [prompts.OMITTED_LINES.format(n=dropped)] + steps[dropped:])
    return text
