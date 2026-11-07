"""The robot service protocol in process: hello, state reads with min_seq, the stream, version mismatch and the
lock (skills/docs/skills.md). The service as a process is tested in tests/integration/test_robot_service.py."""

from __future__ import annotations

import os
import threading
import time
from multiprocessing.connection import Client, Listener

import pytest

from skills import ipc, robot_ipc, robot_service, state_store
from skills.robot_ipc import LinkEvent, RobotClient, StateReply
from skills.schema import RobotState
from skills.state_store import StateStore


def state(height: float) -> RobotState:
    return RobotState(t=0.0, backend="stub", posture="standing", body_height=height)


@pytest.fixture
def service(tmp_path):
    """A store served on a socket by robot_service.serve; yields (socket path, store)."""
    path = str(tmp_path / "r.sock")
    store = StateStore(lambda msg: msg)
    listener = Listener(path, family=ipc.FAMILY)

    def accept() -> None:
        while True:
            try:
                conn = listener.accept()
            except OSError:
                return
            threading.Thread(target=robot_service.serve, args=(conn, store, "stub"), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    yield path, store
    listener.close()


def put_later(store: StateStore, msg: RobotState, delay_s: float) -> None:
    threading.Timer(delay_s, store.put, args=(msg,)).start()


def test_hello_and_state_reads(service):
    path, store = service
    with RobotClient.connect(path) as client:
        assert client.hello["backend"] == "stub"
        empty = client.state()
        assert (empty.state, empty.rx_mono, empty.link) == (None, None, "down")
        store.put(state(0.30))
        latest = client.state()
        assert latest.state is not None and latest.link == "up"
        assert (latest.seq, latest.state.body_height) == (1, 0.30)
        assert latest.state.age_ms is not None and latest.state.age_ms >= 0


def test_read_next_waits_for_a_newer_message(service):
    """min_seq = latest seq + 1: the first message after the request, as decided for comparability."""
    path, store = service
    store.put(state(0.30))
    with RobotClient.connect(path) as client:
        put_later(store, state(0.31), 0.1)
        fresh = client.read_next(wait_s=1.0)
        assert (fresh.seq, fresh.state and fresh.state.body_height) == (2, 0.31)
        t = time.monotonic()
        stale = client.read_next(wait_s=0.1)  # nothing newer comes: the latest earlier message
        assert time.monotonic() - t >= 0.1
        assert stale.seq == 2


def test_stream_sends_new_samples_and_link_changes(service, monkeypatch):
    monkeypatch.setattr(state_store, "LINK_GAP_S", 0.1)
    path, store = service
    store.put(state(0.30))
    with RobotClient.connect(path) as client:
        assert client.subscribe(0.02) == "up"
        first = client.next_event(1.0)
        assert isinstance(first, StateReply) and first.seq == 1
        down = client.next_event(1.0)  # no new message: no sample, then the gap
        assert isinstance(down, LinkEvent) and down.link == "down" and down.rx_mono == first.rx_mono
        store.put(state(0.31))
        up = client.next_event(1.0)
        sample = client.next_event(1.0)
        assert isinstance(up, LinkEvent) and up.link == "up"
        assert isinstance(sample, StateReply) and sample.seq == 2


def test_version_mismatch_is_refused_both_ways(service, tmp_path):
    path, _ = service
    with Client(path, family=ipc.FAMILY) as conn:
        conn.send_bytes(ipc.encode({"v": 99, "op": "hello"}))
        reply = ipc.decode(conn.recv_bytes())
        assert reply["error"]["code"] == robot_ipc.ERR_PROTOCOL_VERSION
        with pytest.raises(EOFError):  # the service closes the connection
            conn.recv_bytes()

    other = str(tmp_path / "other.sock")
    with Listener(other, family=ipc.FAMILY) as listener:

        def answer() -> None:
            with listener.accept() as conn:
                conn.recv_bytes()
                conn.send_bytes(ipc.encode({"v": 2, "op": "hello"}))

        threading.Thread(target=answer, daemon=True).start()
        with pytest.raises(robot_ipc.ProtocolMismatch):
            RobotClient.connect(other)


def test_no_service_is_service_unavailable(tmp_path):
    with pytest.raises(robot_ipc.ServiceUnavailable, match="no robot service at"):
        RobotClient.connect(str(tmp_path / "none.sock"))


def test_lock_admits_one_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(robot_service, "_lock_fd", None)
    lock = str(tmp_path / "robot.lock")
    assert robot_service.acquire_lock(lock)
    held = robot_service._lock_fd
    assert not robot_service.acquire_lock(lock)  # flock is per open file: a second open is refused
    assert held is not None
    os.close(held)  # what process exit does
    assert robot_service.acquire_lock(lock)
    assert robot_service._lock_fd is not None
    os.close(robot_service._lock_fd)
