"""The executor delivers a step's stub fault by class: robot faults in the fault file before arm, skill faults in
GO2_STUB_FAULT (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import threading

import pytest

from contract.env import STUB_FAULT
from contract.faults import fault_file, take_fault
from dispatcher.executor import Executor
from dispatcher.registry import Registry
from dispatcher.tests.helpers import BUILTIN_SKILLS_DIR
from dispatcher.tests.helpers.config import make_config
from tests.helpers import REPO_ROOT


class _Spawned(Exception):
    """Raised in place of the skill's spawn, after the env was captured."""


def _route(tmp_path, monkeypatch, fault):
    """Run one step up to its spawn; returns (fault kind found in the file at arm, skill env)."""
    cfg = make_config(tmp_path, stub={"state_file": str(tmp_path / "missing" / "state.json")})
    ex = Executor(cfg, REPO_ROOT)
    seen: dict = {}

    class Control:
        def arm(self, token, timeout_s):
            seen["at_arm"] = take_fault(cfg.stub.state_file)

    def popen(module, params, env):
        seen["env"] = env
        raise _Spawned

    monkeypatch.setattr(ex, "_control", Control)
    monkeypatch.setattr(ex, "_popen", popen)
    skill = Registry.load(BUILTIN_SKILLS_DIR).get("turn")
    assert skill is not None
    with pytest.raises(_Spawned):
        ex.run(skill, {}, fault=fault, timeout_s=5.0, remaining_task_s=5.0, stop_event=threading.Event())
    assert not fault_file(cfg.stub.state_file).exists()
    return seen["at_arm"], seen["env"]


def test_robot_fault_goes_in_the_fault_file_before_arm(tmp_path, monkeypatch):
    at_arm, env = _route(tmp_path, monkeypatch, "stuck")
    assert at_arm == "stuck" and STUB_FAULT not in env


def test_skill_fault_goes_in_the_skill_env(tmp_path, monkeypatch):
    at_arm, env = _route(tmp_path, monkeypatch, "hang")
    assert at_arm is None and env[STUB_FAULT] == "hang"


def test_no_fault_writes_nothing(tmp_path, monkeypatch):
    at_arm, env = _route(tmp_path, monkeypatch, None)
    assert at_arm is None and STUB_FAULT not in env
