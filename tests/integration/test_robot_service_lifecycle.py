"""The dispatcher's side of the robot service on the stub backend: start, restart, stop, lock, state stream
(docs/running.md, dispatcher/docs/run-log.md)."""

from __future__ import annotations

import json
import os
import signal
import time

import pytest

from dispatcher.executor import Executor
from dispatcher.models import RobotServiceError
from dispatcher.robot import STREAM_PERIOD_S
from dispatcher.tests.helpers import make_config
from tests.helpers import REPO_ROOT
from tests.integration import robot

pytestmark = pytest.mark.integration


@pytest.fixture
def executor(tmp_path):
    ex = Executor(make_config(tmp_path), REPO_ROOT)
    yield ex
    ex.close()


def test_start_read_restart_stop(executor):
    executor.robot.start()
    first = executor.robot.pid
    assert first is not None and executor.fresh_state().posture == "standing"
    os.kill(first, signal.SIGKILL)
    os.waitpid(first, 0)
    state = executor.fresh_state()  # a dead service is started again once, then read
    assert state.posture == "standing" and state.age_ms is not None
    assert executor.robot.pid not in (None, first)
    executor.close()
    assert executor.robot.pid is None and not os.path.exists(executor.robot.socket_path)


def test_lock_held_is_a_clear_startup_error(executor):
    other = robot.start(executor._env(None))  # another service already owns the socket
    try:
        with pytest.raises(RobotServiceError, match="another robot service holds .*robot.lock"):
            executor.robot.start()
    finally:
        robot.stop(other)


def test_state_stream_writes_samples(executor, tmp_path):
    executor.robot.start()
    path = tmp_path / "task.state_stream.jsonl"
    stream = executor.state_stream(path, time.monotonic())
    time.sleep(5 * STREAM_PERIOD_S)
    stream.close()
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "stream_start" and lines[0]["link"] == "up"
    samples = [r for r in lines if r["type"] == "sample"]
    assert len(samples) >= 2 and samples[0]["state"]["posture"] == "standing"
    assert samples[-1]["state"]["seq"] > samples[0]["state"]["seq"]
