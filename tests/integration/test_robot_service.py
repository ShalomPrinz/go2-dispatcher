"""The robot service as a process on the stub backend: skills sample state through it, one service per
socket, orphan exit (skills/docs/skills.md)."""

from __future__ import annotations

import os

import pytest
from pydantic import TypeAdapter

from skills import robot_service
from skills.schema import SkillResponse
from tests.helpers import run_module, single_response, stub_env
from tests.integration import robot

pytestmark = pytest.mark.integration

RESPONSE = TypeAdapter(SkillResponse)


def test_skill_samples_state_through_the_service(tmp_path):
    """sit changes the stub posture: state_after is a message the service read after the change."""
    with robot.running(stub_env(tmp_path)) as env:
        proc = run_module("sit", {}, env)
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.status == "ok" and resp.state_error is None, proc.stderr
    before, after = resp.state_before, resp.state_after
    assert before is not None and after is not None
    assert (before.posture, after.posture) == ("standing", "sitting")
    assert before.seq is not None and after.seq is not None and after.seq > before.seq
    assert before.age_ms is not None and before.age_ms < 1000.0


def test_skill_without_service_reports_state_error(tmp_path):
    proc = run_module("sit", {}, robot.with_socket(stub_env(tmp_path)))
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.status == "ok"  # a failed sample does not fail the step
    assert resp.state_before is None and resp.state_error is not None
    assert resp.state_error.startswith("before: ServiceUnavailable: no robot service at ")


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


def test_service_exits_when_parent_dies(tmp_path):
    """The service shares the skills' orphan watchdog (skills/process.py)."""
    env = robot.with_socket(stub_env(tmp_path, GO2_PARENT_PID=str(os.getppid() or 1)))
    proc = robot.start(env)
    try:
        assert proc.wait(timeout=5) == 137
    finally:
        robot.stop(proc)
