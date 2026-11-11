"""Transports (CLI, Telegram) over the dispatcher: shared setup and output (docs/running.md)."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable

from skills import stub

from .. import process_lock
from ..config import API_KEY_ENV, Config, load_config_and_env
from ..context import PromptSurface
from ..dispatcher import Dispatcher
from ..executor import Executor, needs_vision
from ..llm import AnthropicPlanner, OpenRouterPlanner, PlannerClient
from ..models import ConfigError, RegistryError, StartupError
from ..registry import Registry
from ..runlog import TaskObserver

__all__ = [
    "load_config_and_env",
    "build_dispatcher",
    "startup_error_text",
    "make_planner",
    "API_KEY_ENV",
    "TEST_PLANNER_ENV",
]

TEST_PLANNER_ENV = "GO2_TEST_PLANNER"  # "module:factory" (tests only)
MISSING_API_KEY = "Missing {env}."


def startup_error_text(e: StartupError) -> str:
    """Stderr line for a start-up error; the entry point prints it and exits 2 (docs/running.md)."""
    if isinstance(e, ConfigError):
        return f"Config error: {e}"
    if isinstance(e, RegistryError):
        return f"Registry error: {e}"
    return str(e)


def make_planner(cfg: Config, surface: PromptSurface) -> PlannerClient:
    """Choose the planner for ``surface``: test planner, else the ``llm.provider`` one (docs/running.md).
    Raises ``StartupError`` for a malformed test planner spec or a missing API key."""
    spec = os.environ.get(TEST_PLANNER_ENV)
    if spec:
        module_name, sep, attr = spec.partition(":")
        if not sep or not module_name or not attr:
            raise StartupError(f"{TEST_PLANNER_ENV} must be module:factory, got {spec!r}.")
        factory = getattr(importlib.import_module(module_name), attr)
        return factory(surface)
    env = API_KEY_ENV[cfg.llm.provider]
    key = os.environ.get(env, "").strip()
    if not key:
        raise StartupError(MISSING_API_KEY.format(env=env))
    if cfg.llm.provider == "openrouter":
        return OpenRouterPlanner(key, cfg.llm, surface)
    return AnthropicPlanner(key, cfg.llm, surface)


def build_dispatcher(
    cfg: Config, *, reset_stub: bool, observer: Callable[[Registry], TaskObserver] | None = None
) -> Dispatcher:
    """Lock, registry, stub reset, planner, executor, robot service start, dispatcher, vision service start
    (docs/running.md). ``observer`` builds the dispatcher's task observer from the registry. Raises
    ``StartupError``; process-lifetime hooks are the caller's, and ``Dispatcher.shutdown`` stops both services."""
    process_lock.acquire(cfg.log.dir)  # the root, so all provider/backend folders share one lock
    registry = Registry.load(cfg.skills.dir)
    if reset_stub and cfg.robot.backend == "stub":
        stub.reset(cfg.stub.initial_posture, cfg.stub.state_file)
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    planner = make_planner(cfg, surface)
    executor = Executor(cfg, cfg.base_dir)
    executor.start_robot()  # waits for ready, opens the control connection; raises RobotServiceError (docs/running.md)
    try:
        dispatcher = Dispatcher(
            cfg,
            registry,
            planner,
            executor,
            observer=observer(registry) if observer is not None else None,
        )
    except BaseException:
        executor.close()  # no dispatcher, so no shutdown hook stops the robot service
        raise
    if any(needs_vision(registry[name]) for name in registry.names()):
        executor.vision.start()  # no wait: the first vision step waits for ready (docs/running.md)
    return dispatcher
