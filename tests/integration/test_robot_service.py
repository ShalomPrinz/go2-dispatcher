"""The robot service as a process on the stub backend: skills sample state and move through it, a killed skill or
a gone dispatcher stops the robot, the service faults, one service per socket (skills/docs/skills.md)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from skills import robot_service, stub
from skills.schema import SkillResponse
from tests.helpers import REPO_ROOT, run_module, single_response, stub_env
from tests.integration import robot

pytestmark = pytest.mark.integration

RESPONSE = TypeAdapter(SkillResponse)
LONG_WALK = {"direction": "forward", "distance_m": 1.0}  # 4.4 s at time scale 1


def until(check, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def command(tmp_path: Path):
    return stub.read_state(tmp_path / "stub_state.json").command


def start_skill(name: str, params: dict, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", f"skills.{name}", json.dumps(params)],
        env=env,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def test_skill_samples_state_through_the_service(tmp_path):
    """sit changes the stub posture: state_after is a message the service read after the change."""
    with robot.running(stub_env(tmp_path)) as svc:
        proc = run_module("sit", {}, svc.arm())
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.status == "ok" and resp.state_error is None, proc.stderr
    before, after = resp.state_before, resp.state_after
    assert before is not None and after is not None
    assert (before.posture, after.posture) == ("standing", "sitting")
    assert before.seq is not None and after.seq is not None and after.seq > before.seq
    assert before.age_ms is not None and before.age_ms < 1000.0


def test_skill_without_service(tmp_path):
    proc = run_module("sit", {}, robot.with_socket(stub_env(tmp_path)))
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.error is not None and resp.error.code == "robot_unavailable"
    assert resp.state_before is None and resp.state_error is not None
    assert resp.state_error.startswith("before: ServiceUnavailable: no robot service at ")


def test_killed_skill_stops_the_robot(tmp_path):
    """The lease's connection closes when the skill dies: the service sends StopMove itself."""
    with robot.running(stub_env(tmp_path, GO2_STUB_TIME_SCALE="1")) as svc:
        walk = start_skill("walk", LONG_WALK, svc.arm())
        try:
            assert until(lambda: command(tmp_path) is not None)
        finally:
            walk.kill()
            walk.communicate()
        assert until(lambda: command(tmp_path) is None, 1.0)
        assert svc.proc.poll() is None


def test_service_stops_the_robot_when_the_dispatcher_dies(tmp_path):
    """The orphan watchdog sends StopMove before the service exits."""
    stub.write_command((0.3, 0.0, 0.0), tmp_path / "stub_state.json")  # a robot left moving
    env = robot.with_socket(stub_env(tmp_path, GO2_PARENT_PID=str(os.getppid() or 1)))
    proc = robot.start(env)
    try:
        assert proc.wait(timeout=5) == 137
    finally:
        robot.stop(proc)
    assert command(tmp_path) is None


def test_service_crash_leaves_the_robot_moving_until_the_fallback_stop(tmp_path):
    with robot.running(stub_env(tmp_path, GO2_STUB_TIME_SCALE="1")) as svc:
        proc = run_module("walk", LONG_WALK, {**svc.arm(), "GO2_STUB_FAULT": "service_crash"})
        assert svc.proc.wait(timeout=5) == stub.CRASH_EXIT_CODE
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.error is not None and resp.error.code == "robot_unavailable"
    assert command(tmp_path) == (1.0 * 0.3, 0.0, 0.0)  # worst case: the robot keeps its last velocity
    stop = RESPONSE.validate_python(single_response(run_module("stop_move", {}, stub_env(tmp_path))))
    assert stop.status == "ok" and command(tmp_path) is None


def test_service_hang_stops_the_whole_service(tmp_path):
    with robot.running(stub_env(tmp_path, GO2_STUB_TIME_SCALE="1")) as svc:
        walk = start_skill("walk", LONG_WALK, {**svc.arm(), "GO2_STUB_FAULT": "service_hang"})
        try:
            stopped = until(lambda: Path(f"/proc/{svc.proc.pid}/stat").read_text().split()[2] == "T")
        finally:
            walk.kill()
            walk.communicate()
        assert stopped and command(tmp_path) is not None
        stop = RESPONSE.validate_python(single_response(run_module("stop_move", {}, stub_env(tmp_path))))
        assert stop.status == "ok" and command(tmp_path) is None  # the fallback works beside a hung service
        os.kill(svc.proc.pid, signal.SIGKILL)


def test_second_service_is_refused(tmp_path):
    env = robot.with_socket(stub_env(tmp_path))
    first = robot.start(env)
    try:
        second = robot.start(env, wait_ready=False)
        assert second.wait(timeout=10) == robot_service.EXIT_LOCKED
        robot.stop(second)
        assert first.poll() is None and os.path.exists(env["GO2_ROBOT_SOCKET"])
    finally:
        robot.stop(first)
    assert not os.path.exists(env["GO2_ROBOT_SOCKET"])  # SIGTERM removes the socket file
