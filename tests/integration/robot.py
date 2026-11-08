"""Start the robot service for tests that run skills as subprocesses, and arm their leases on a control
connection as the dispatcher does (skills/docs/skills.md)."""

from __future__ import annotations

import selectors
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from skills import robot_service
from skills.env import LEASE_TOKEN, ROBOT_SOCKET, STUB_STATE_FILE
from skills.robot_ipc import RobotClient
from tests.helpers import REPO_ROOT

READY_WAIT_S = 10.0
ARM_TIMEOUT_S = 30.0  # a lease longer than any test skill run


def with_socket(env: dict[str, str]) -> dict[str, str]:
    """A ``stub_env`` env with ``GO2_ROBOT_SOCKET`` next to its stub state file."""
    return {**env, ROBOT_SOCKET: str(Path(env[STUB_STATE_FILE]).parent / "robot.sock")}


def start(env: dict[str, str], *, wait_ready: bool = True) -> subprocess.Popen:
    """Start ``python -m skills.robot_service`` with ``env``; by default wait for its ready line."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "skills.robot_service"],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    if not wait_ready:
        return proc
    assert proc.stdout is not None
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        ready = sel.select(READY_WAIT_S) and proc.stdout.readline().strip() == robot_service.READY_LINE
    if not ready:
        stop(proc)
        raise AssertionError(f"robot service not ready within {READY_WAIT_S:g}s (exit {proc.returncode})")
    return proc


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(robot_service.STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if proc.stdout is not None:
        proc.stdout.close()


@dataclass
class Service:
    """A running service with the test's control connection; ``env`` has ``GO2_ROBOT_SOCKET`` set."""

    proc: subprocess.Popen
    env: dict[str, str]
    control: RobotClient

    def arm(self, env: dict[str, str] | None = None, timeout_s: float = ARM_TIMEOUT_S) -> dict[str, str]:
        """Arm a lease for one skill run; returns ``env`` (default: the service's) with its token."""
        token = uuid.uuid4().hex
        self.control.arm(token, timeout_s)
        return {**(env or self.env), LEASE_TOKEN: token}


@contextmanager
def running(env: dict[str, str]) -> Iterator[Service]:
    """A service for a ``stub_env`` skill env, with a control connection open."""
    env = with_socket(env)
    proc = start(env)
    try:
        with RobotClient.connect(env[ROBOT_SOCKET], control=True) as control:
            yield Service(proc, env, control)
    finally:
        stop(proc)
