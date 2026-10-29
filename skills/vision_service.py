"""The vision service (skills/docs/skills.md): a long-running process that loads the detector once, warms it
up and answers detect_object's requests on a Unix socket. Reads the camera only; never commands motion.

    python -m skills.vision_service

Started and stopped by the dispatcher; prints one ``ready`` line on stdout once it accepts requests."""

from __future__ import annotations

import os
import signal
import sys
import traceback
from multiprocessing.connection import Listener
from types import FrameType
from typing import NoReturn, Protocol

from skills import backend, process, vision_ipc
from skills.backend import DetectResult
from skills.env import VISION_SOCKET

READY_LINE = "ready"
STARTUP_TIMEOUT_S = 30.0  # torch import + YOLO load + first predict + DDS init on the lab machine (tunable)
STOP_GRACE_S = 2.0  # SIGTERM to SIGKILL (tunable)
REQUEST_WAIT_S = 1.0  # a connection that sends no request in this time is dropped (tunable)
SOCKET_MODE = 0o600
EXIT_CONFIG_ERROR = 2


class Detector(Protocol):
    def warm_up(self) -> None: ...

    def detect(self, target: str) -> DetectResult: ...


def make_detector() -> Detector:
    """The configured backend's detector; raises BackendNotConfigured."""
    if backend.backend_name() == "real":
        from skills.real_detector import RealDetector

        return RealDetector()
    from skills.stub import StubDetector

    return StubDetector()


def handle(conn: vision_ipc.Conn, detector: Detector) -> None:
    """Answer one request on ``conn``; a client that is gone or silent is dropped."""
    try:
        if not conn.poll(REQUEST_WAIT_S):
            return
        request = vision_ipc.decode(conn.recv_bytes(vision_ipc.MAX_MESSAGE_BYTES))
    except (OSError, EOFError, ValueError):
        traceback.print_exc(file=sys.stderr)
        return
    try:
        target = request["target"]
        if not isinstance(target, str):
            raise TypeError("target is not a string")
        reply = vision_ipc.ok_reply(detector.detect(target))
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        reply = vision_ipc.error_reply(e)
    try:
        conn.send_bytes(vision_ipc.encode(reply))
    except OSError:
        traceback.print_exc(file=sys.stderr)


def _on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(0)


def main() -> None:
    process.capture_stdout()
    process.start_orphan_watchdog()
    path = os.environ.get(VISION_SOCKET, "")
    if not path:
        print(f"vision service: {VISION_SOCKET} is not set", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        detector = make_detector()
    except backend.BackendNotConfigured as e:
        print(f"vision service: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    try:
        detector.warm_up()
    except Exception:  # each request retries the part that failed and reports its error code
        traceback.print_exc(file=sys.stderr)
    if os.path.exists(path):  # left by a service that was killed
        os.unlink(path)
    signal.signal(signal.SIGTERM, _on_sigterm)
    with Listener(path, family=vision_ipc.FAMILY) as listener:  # closing it removes the socket file
        os.chmod(path, SOCKET_MODE)
        process.write_raw_stdout(READY_LINE + "\n")
        print(f"vision service: ready on {path}", file=sys.stderr, flush=True)
        while True:
            with listener.accept() as conn:
                handle(conn, detector)


if __name__ == "__main__":
    main()
