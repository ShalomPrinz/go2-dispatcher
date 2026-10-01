"""Executor on real subprocesses with the stub backend (docs/safety.md, testing.md)."""

from __future__ import annotations

import os
import threading
import time

import pytest

from go2_dispatcher.executor import Executor
from go2_dispatcher.registry import Registry, SkillDescriptor
from go2_skills import stub
from helpers import REPO_ROOT, make_config
from helpers.skill_modules.env_dump import POLICY as ENV_DUMP_POLICY

pytestmark = pytest.mark.integration

TURN = {"direction": "left", "angle_deg": 30}
LONG_TIMEOUT_S = 20.0
LONG_REMAINING_S = 60.0


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def executor(cfg):
    return Executor(cfg, REPO_ROOT)


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills")


def run(executor, registry, skill="turn", params=TURN, *, fault=None,
        timeout_s=LONG_TIMEOUT_S, remaining_task_s=LONG_REMAINING_S, stop_event=None):
    return executor.run(registry.get(skill), params, fault=fault, timeout_s=timeout_s,
                        remaining_task_s=remaining_task_s,
                        stop_event=stop_event or threading.Event())


def assert_group_gone(pid):
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_ok(executor, registry):
    res = run(executor, registry)
    assert res.outcome == "ok", res
    assert res.response is not None and res.response.skill == "turn"
    assert res.exit_code == 0 and res.pid and res.duration_ms > 0
    assert res.stop_move is None and res.error_code is None


def test_fault_error(executor, registry):
    res = run(executor, registry, fault="error")
    assert res.outcome == "error"
    assert res.error_code == "sdk_error" and res.error_message
    assert res.exit_code == 1 and res.stop_move is None


def test_fault_crash(executor, registry):
    res = run(executor, registry, fault="crash")
    assert res.outcome == "malformed" and res.exit_code == 139
    assert res.error_code == "malformed"
    assert res.error_message == "skill process exited with code 139 without a valid response"
    assert res.response is None


def test_fault_garbage(executor, registry):
    res = run(executor, registry, fault="garbage")
    assert res.outcome == "malformed" and res.error_code == "malformed"
    assert res.exit_code == 0 and res.response is None


def test_wrong_skill_name_is_malformed(executor, registry):
    d = registry.get("turn")
    other = SkillDescriptor(name="walk", entrypoint=d.entrypoint, description=d.description,
                            params=d.params, policy=d.policy)
    res = executor.run(other, TURN, fault=None, timeout_s=LONG_TIMEOUT_S,
                       remaining_task_s=LONG_REMAINING_S, stop_event=threading.Event())
    assert res.outcome == "malformed"


def test_hang_timeout(executor, registry):
    t0 = time.monotonic()
    res = run(executor, registry, fault="hang", timeout_s=1.0)
    assert time.monotonic() - t0 < 1.0 + 5.0
    assert res.outcome == "timeout" and res.interrupt_cause is None
    assert res.error_code == "timeout" and res.error_message == "killed after 1s timeout"
    assert_group_gone(res.pid)
    assert res.stop_move is not None and res.stop_move.ok
    assert res.stop_move.reason == "step_timeout"
    assert res.stop_move.response.state_after.posture == "standing"


def test_kill_current_operator(executor, registry):
    assert executor.kill_current("operator") is False      # nothing running
    killed = []
    timer = threading.Timer(0.5, lambda: killed.append(executor.kill_current("operator")))
    timer.start()
    t0 = time.monotonic()
    res = run(executor, registry, fault="hang")
    elapsed = time.monotonic() - t0
    timer.join()
    assert killed == [True]
    assert res.outcome == "interrupted" and res.interrupt_cause == "operator"
    assert res.error_code == "stopped_by_operator" and res.error_message == "stopped by operator"
    assert elapsed < 2.0 + 0.5
    assert res.stop_move is not None and res.stop_move.reason == "operator"
    assert_group_gone(res.pid)
    assert executor.kill_current("operator") is False


def test_stop_event_operator(executor, registry):
    ev = threading.Event()
    threading.Timer(0.3, ev.set).start()
    res = run(executor, registry, fault="hang", stop_event=ev)
    assert res.outcome == "interrupted" and res.interrupt_cause == "operator"
    assert res.stop_move is not None


def test_task_time_limit(executor, registry):
    res = run(executor, registry, fault="hang", remaining_task_s=0.5)
    assert res.outcome == "interrupted" and res.interrupt_cause == "task_time_limit"
    assert res.error_code == "task_time_limit"
    assert res.error_message == "task time limit reached"
    assert res.stop_move is not None and res.stop_move.reason == "task_time_limit"


def test_shutdown_cause(executor, registry):
    threading.Timer(0.3, lambda: executor.kill_current("shutdown")).start()
    res = run(executor, registry, fault="hang")
    assert res.outcome == "interrupted" and res.interrupt_cause == "shutdown"
    assert res.error_code == "shutdown" and res.error_message == "dispatcher shutting down"


def test_child_env_has_no_secrets(executor, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-secret")
    monkeypatch.setenv("GO2_STUB_FAULT", "crash")            # must not leak either
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "tests" / "helpers"))
    desc = SkillDescriptor(name="env_dump", entrypoint="skill_modules.env_dump",
                           description="dump env keys", params={}, policy=ENV_DUMP_POLICY)
    res = executor.run(desc, {}, fault=None, timeout_s=LONG_TIMEOUT_S,
                       remaining_task_s=LONG_REMAINING_S, stop_event=threading.Event())
    assert res.outcome == "ok", res
    keys = res.response.observations["env_keys"]
    assert "ANTHROPIC_API_KEY" not in keys and "TELEGRAM_BOT_TOKEN" not in keys
    assert "GO2_STUB_FAULT" not in keys
    for k in ("PYTHONUNBUFFERED", "GO2_BACKEND", "GO2_PARENT_PID", "GO2_STUB_STATE_FILE"):
        assert k in keys


def test_read_state(executor, cfg):
    stub.write_posture("sitting", cfg.stub.state_file)
    state = executor.read_state()
    assert state is not None and state.backend == "stub" and state.posture == "sitting"
    stub.write_posture("standing", cfg.stub.state_file)
    assert executor.read_state().posture == "standing"


def test_stop_move_direct(executor):
    res = executor.stop_move(reason="internal_error")
    assert res.ok and res.reason == "internal_error" and res.exit_code == 0
    assert res.response.skill == "stop_move"
    assert executor.kill_current("operator") is False       # never registered as current


def test_stop_move_never_raises(executor, monkeypatch):
    def boom(*a, **k):
        raise OSError("cannot spawn")

    monkeypatch.setattr(executor, "_popen", boom)
    smr = executor.stop_move("internal_error")
    assert smr.ok is False and smr.reason == "internal_error"
    assert "OSError: cannot spawn" in smr.stderr_tail
