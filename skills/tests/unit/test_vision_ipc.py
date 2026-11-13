"""The vision service protocol in process: replies, error codes and a silent service (skills/docs/skills.md)."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from multiprocessing.connection import Listener

import pytest

from contract import wire
from contract.schema import ErrorCode
from skills import vision_ipc, vision_service
from skills.backend import CameraUnavailable, DetectResult, VisionUnavailable


class FakeDetector:
    def warm_up(self) -> None:
        pass

    def detect(self, target: str) -> DetectResult:
        if target == "cup":
            raise CameraUnavailable("GetImageSample returned 3104")
        return DetectResult(found=True, position="left", closeness="far", confidence=0.7)


@contextmanager
def serving(path, answer: bool) -> Iterator[None]:
    """Serve one connection; a silent server holds it open until the block ends. Exit waits for the socket to close."""
    listener = Listener(str(path), family=wire.FAMILY)
    done = threading.Event()

    def run() -> None:
        with listener, listener.accept() as conn:
            if answer:
                vision_service.handle(conn, FakeDetector())
            else:
                done.wait(5.0)  # answer nothing

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join(5.0)  # the listener is closed and its socket file removed before the path is reused


def ask(path, target: str, wait_s: float = 2.0) -> DetectResult:
    with vision_ipc.connect(str(path)) as conn:
        return vision_ipc.detect(conn, target, wait_s)


def test_result_and_error_round_trip(tmp_path):
    path = tmp_path / "v.sock"
    with serving(path, answer=True):
        assert ask(path, "chair") == DetectResult(found=True, position="left", closeness="far", confidence=0.7)
    with serving(path, answer=True), pytest.raises(vision_ipc.ServiceError) as err:
        ask(path, "cup")
    assert err.value.code == ErrorCode.CAMERA_UNAVAILABLE
    assert vision_ipc.error_message(err.value) == "CameraUnavailable: GetImageSample returned 3104"


def test_silent_service_is_vision_unavailable(tmp_path):
    path = tmp_path / "v.sock"
    with serving(path, answer=False), pytest.raises(VisionUnavailable, match="no reply within 0.2s"):
        ask(path, "chair", wait_s=0.2)
