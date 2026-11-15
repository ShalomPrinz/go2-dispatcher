"""Composition root: builds and wires the dispatcher and its parts for the transports (docs/running.md)."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from pathlib import Path

from contract.env import robot_socket_path
from services.robot import stub
from services.robot.protocol import READ_STATE, STOP_MOVE

from . import process_lock
from .config import API_KEY_ENV, Config
from .context import build_prompt_surface
from .dispatcher import Dispatcher
from .execution import Executor
from .execution.control import ControlLink
from .execution.env import ChildEnv
from .execution.process import run_utility
from .execution.robot_service import RobotSupervisor
from .execution.robot_service import log_path as robot_log_path
from .execution.stop_path import StopPath
from .execution.vision_service import VisionSupervisor
from .execution.vision_service import log_path as vision_log_path
from .llm.anthropic_planner import AnthropicPlanner
from .llm.client import PlannerClient
from .llm.openrouter_planner import OpenRouterPlanner
from .llm.surface import PromptSurface
from .models import RobotState, StartupError
from .registry import Registry
from .runlog import TaskObserver

__all__ = [
    "build_dispatcher",
    "build_executor",
    "load_prompt_surface",
    "make_planner",
    "read_robot_state",
    "reset_stub",
    "MISSING_API_KEY",
    "STOP_MOVE_TIMEOUT_S",
    "TEST_PLANNER_ENV",
]

TEST_PLANNER_ENV = "GO2_TEST_PLANNER"  # "module:factory" (tests only)
MISSING_API_KEY = "Missing {env}."
STOP_MOVE_TIMEOUT_S = STOP_MOVE.policy.timeout_s({})  # the bot's shutdown backstop waits this + its margin


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


def _execution(cfg: Config, cwd: Path | None) -> tuple[Executor, VisionSupervisor]:
    cwd = Path(cwd if cwd is not None else cfg.base_dir)
    env = ChildEnv(cfg)
    vision = VisionSupervisor(cwd, vision_log_path(cfg.log.dir), env.for_service)
    robot = RobotSupervisor(cwd, robot_log_path(cfg.log.dir), robot_socket_path(cfg.log.dir), env.for_service)
    control = ControlLink(robot)
    return Executor(cfg, env, robot, vision, control, StopPath(control, robot, env, cwd), cwd), vision


def build_executor(cfg: Config, cwd: Path | None = None) -> Executor:
    """The executor with its parts; processes start in ``cwd`` (default ``cfg.base_dir``). Starts nothing."""
    return _execution(cfg, cwd)[0]


def build_dispatcher(
    cfg: Config, *, reset_stub: bool, observer: Callable[[Registry], TaskObserver] | None = None
) -> Dispatcher:
    """Lock, registry, stub reset, planner, executor, robot service start, dispatcher, vision service start
    (docs/running.md). ``observer`` builds the dispatcher's task observer from the registry. Raises
    ``StartupError``; process-lifetime hooks are the caller's, and ``Dispatcher.shutdown`` stops both services."""
    process_lock.acquire(cfg.log.dir)  # the root, so all provider/backend folders share one lock
    registry = Registry.load(cfg.skills.dir)
    if reset_stub and cfg.robot.backend == "stub":
        _reset(cfg)
    surface = build_prompt_surface(registry, cfg.loop.planning_horizon)
    planner = make_planner(cfg, surface)
    executor, vision = _execution(cfg, None)
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
    if any(registry[name].uses_vision for name in registry.names()):
        vision.start()  # no wait: the first vision step waits for ready (docs/running.md)
    return dispatcher


def load_prompt_surface(cfg: Config) -> PromptSurface:
    """The prompt surface of ``skills.dir`` without a dispatcher (``go2 catalog``); raises ``RegistryError``."""
    return build_prompt_surface(Registry.load(cfg.skills.dir), cfg.loop.planning_horizon)


def read_robot_state(cfg: Config) -> RobotState | None:
    """The ``read_state`` utility's state, read with its own DDS, not through the robot service (``go2 state``)."""
    response = run_utility(READ_STATE, ChildEnv(cfg).for_service(), cfg.base_dir).response
    return response.state_after if response is not None else None


def reset_stub(cfg: Config) -> None:
    """Take the lock and reset the stub to ``stub.initial_posture`` (``go2 --reset-stub``); the caller checks
    the backend. Raises ``StartupError`` when another dispatcher holds the lock."""
    process_lock.acquire(cfg.log.dir)
    _reset(cfg)


def _reset(cfg: Config) -> None:
    stub.reset_stub(cfg.stub.initial_posture, cfg.stub.state_file)
