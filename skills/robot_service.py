"""The robot service (skills/docs/skills.md): a long-running process, the only DDS owner of the robot's sport
state. It keeps the latest rt/sportmodestate message (real) or the stub's state (sim) and answers state reads
and stream subscriptions on a Unix socket. Read-only: it never creates a SportClient.

    python -m skills.robot_service

Started by the dispatcher; prints one ``ready`` line on stdout once it accepts connections."""

from __future__ import annotations

import fcntl
import math
import os
import signal
import sys
import threading
import time
import traceback
from multiprocessing.connection import Listener
from types import FrameType
from typing import NoReturn

from skills import backend, ipc, process, robot_ipc, stub
from skills.env import ROBOT_SOCKET, robot_lock_path
from skills.schema import RobotState
from skills.state_store import StateStore

READY_LINE = "ready"
STARTUP_TIMEOUT_S = 10.0  # SDK import, DDS init and subscription on the lab machine (tunable)
STOP_GRACE_S = 2.0  # SIGTERM to SIGKILL (tunable)
SIM_PERIOD_S = 0.02  # the sim publishes the stub state at 50 Hz, the rate assumed for the robot (tunable)
SOCKET_MODE = 0o600
EXIT_CONFIG_ERROR = 2
EXIT_LOCKED = 3

_lock_fd: int | None = None  # held for the process lifetime; the kernel releases it on any exit


def acquire_lock(path: str) -> bool:
    """Take the exclusive lock on ``path`` for this process; False if another process holds it."""
    global _lock_fd
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    _lock_fd = fd
    return True


# --- state sources ----------------------------------------------------------------


def _same(state: RobotState) -> RobotState:
    return state


def _simulate(store: StateStore) -> None:
    while True:
        try:
            store.put(stub.sample_state())
        except Exception:  # a bad state file or objects env: no message, as on a lost link
            traceback.print_exc(file=sys.stderr)
        time.sleep(SIM_PERIOD_S)


def start_source(name: str) -> tuple[StateStore, object]:
    """The store fed by the backend's state messages, and what must stay referenced to keep them coming.
    real: the rt/sportmodestate subscriber; sim (stub): a thread sampling the stub state every SIM_PERIOD_S."""
    if name == "real":
        from skills import real

        store = StateStore(real.state_from_msg)
        return store, real.subscribe_state(store.put)
    store = StateStore(_same)
    thread = threading.Thread(target=_simulate, args=(store,), name="sim-state", daemon=True)
    thread.start()
    return store, thread


# --- connections ------------------------------------------------------------------


def _send(conn: ipc.Conn, msg: dict) -> bool:
    try:
        conn.send_bytes(ipc.encode(msg))
        return True
    except (OSError, ValueError):
        return False


def _number(request: dict, key: str) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{key} must be a finite number >= 0, got {value!r}")
    return float(value)


def _min_seq(request: dict) -> int | None:
    value = request.get("min_seq")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"min_seq must be an integer or null, got {value!r}")
    return value


def stream(conn: ipc.Conn, store: StateStore, period_s: float) -> None:
    """Send each new message at most once per ``period_s`` and every link change, until the client goes."""
    link, _ = store.link()
    if not _send(conn, robot_ipc.message("subscribed", period_ms=round(period_s * 1000.0, 3), link=link)):
        return
    sent_seq = 0
    while True:
        time.sleep(period_s)
        now, rx_mono = store.link()
        if now != link:
            link = now
            if not _send(conn, robot_ipc.message("link", link=link, rx_mono=rx_mono)):
                return
        reading = store.read()
        if reading is not None and reading.state.seq != sent_seq:
            sent_seq = reading.state.seq or 0
            if not _send(conn, robot_ipc.state_message("sample", reading)):
                return


def serve(conn: ipc.Conn, store: StateStore, backend_name: str) -> None:
    """Answer requests on one persistent connection until the client closes it; a bad request closes it."""
    with conn:
        while True:
            try:
                request = ipc.decode(conn.recv_bytes(robot_ipc.MAX_MESSAGE_BYTES))
            except (OSError, EOFError):
                return
            except ValueError as e:
                _send(conn, robot_ipc.error_message(robot_ipc.ERR_BAD_REQUEST, f"not a JSON object: {e}"))
                return
            if request.get("v") != robot_ipc.PROTOCOL_VERSION:
                text = f"protocol version {request.get('v')!r} is not {robot_ipc.PROTOCOL_VERSION}"
                _send(conn, robot_ipc.error_message(robot_ipc.ERR_PROTOCOL_VERSION, text))
                return
            op = request.get("op")
            try:
                if op == "hello":
                    reply = robot_ipc.message("hello", backend=backend_name, pid=os.getpid())
                elif op == "state":
                    wait_s = min(_number(request, "wait_ms") / 1000.0, robot_ipc.MAX_STATE_WAIT_S)
                    reply = robot_ipc.state_message("state", store.read(_min_seq(request), wait_s))
                elif op == "subscribe":
                    period_s = max(_number(request, "period_ms") / 1000.0, robot_ipc.MIN_STREAM_PERIOD_S)
                    stream(conn, store, period_s)
                    return
                else:
                    raise ValueError(f"unknown op {op!r}")
            except ValueError as e:
                _send(conn, robot_ipc.error_message(robot_ipc.ERR_BAD_REQUEST, str(e)))
                return
            if not _send(conn, reply):
                return


def _on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(0)


def _fail(text: str, code: int) -> NoReturn:
    print(f"robot service: {text}", file=sys.stderr)
    sys.exit(code)


def main() -> None:
    process.capture_stdout()
    process.start_orphan_watchdog()
    path = os.environ.get(ROBOT_SOCKET, "")
    if not path:
        _fail(f"{ROBOT_SOCKET} is not set", EXIT_CONFIG_ERROR)
    try:
        name = backend.backend_name()
    except backend.BackendNotConfigured as e:
        _fail(str(e), EXIT_CONFIG_ERROR)
    lock = robot_lock_path(path)
    if not acquire_lock(lock):
        _fail(f"another robot service holds {lock}", EXIT_LOCKED)
    try:
        store, _source = start_source(name)
    except backend.BackendNotConfigured as e:
        _fail(str(e), EXIT_CONFIG_ERROR)
    if os.path.exists(path):  # left by a service that was killed; the lock says none is running
        os.unlink(path)
    signal.signal(signal.SIGTERM, _on_sigterm)
    with Listener(path, family=ipc.FAMILY) as listener:  # closing it removes the socket file
        os.chmod(path, SOCKET_MODE)
        process.write_raw_stdout(READY_LINE + "\n")
        print(f"robot service: {name} ready on {path}", file=sys.stderr, flush=True)
        while True:
            try:
                conn = listener.accept()
            except OSError:
                traceback.print_exc(file=sys.stderr)
                continue
            threading.Thread(target=serve, args=(conn, store, name), name="robot-conn", daemon=True).start()


if __name__ == "__main__":
    main()
