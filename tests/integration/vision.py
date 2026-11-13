"""Start the vision service for tests that run detect_object as a subprocess (skills/docs/skills.md)."""

from __future__ import annotations

import selectors
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from contract.env import STUB_STATE_FILE, VISION_SOCKET
from skills import vision_service
from tests.helpers import REPO_ROOT

READY_WAIT_S = 10.0


def start(env: dict[str, str]) -> subprocess.Popen:
    """Start ``python -m skills.vision_service`` with ``env`` and wait for its ready line."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "skills.vision_service"],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    with selectors.DefaultSelector() as sel:
        sel.register(proc.stdout, selectors.EVENT_READ)
        ready = sel.select(READY_WAIT_S) and proc.stdout.readline().strip() == vision_service.READY_LINE
    if not ready:
        stop(proc)
        raise AssertionError(f"vision service not ready within {READY_WAIT_S:g}s (exit {proc.returncode})")
    return proc


def stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(vision_service.STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if proc.stdout is not None:
        proc.stdout.close()


@contextmanager
def running(env: dict[str, str]) -> Iterator[dict[str, str]]:
    """A service for a ``stub_env`` skill env; yields that env with ``GO2_VISION_SOCKET`` set."""
    socket = Path(env[STUB_STATE_FILE]).parent / "vision.sock"
    env = {**env, VISION_SOCKET: str(socket)}
    proc = start(env)
    try:
        yield env
    finally:
        stop(proc)
