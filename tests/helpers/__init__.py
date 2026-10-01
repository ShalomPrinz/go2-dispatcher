"""Test helpers (docs/testing.md)."""

from .config import REPO_ROOT, make_config
from .fake_telegram import fake_context, fake_update, replies
from .fakes import FakeClock, FakeExecutor, exec_result, stop_move_result
from .planner import SCRIPTED_USAGE, ScriptedPlanner
from .skill_process import run_module, single_response, stub_env

__all__ = ["REPO_ROOT", "make_config", "ScriptedPlanner", "SCRIPTED_USAGE", "run_module",
           "single_response", "stub_env", "FakeClock", "FakeExecutor", "exec_result",
           "stop_move_result", "fake_update", "fake_context", "replies"]
