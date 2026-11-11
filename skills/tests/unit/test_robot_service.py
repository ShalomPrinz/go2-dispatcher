"""The robot service protocol in process: hello, state reads with min_seq, the stream, version mismatch, the lock,
and the lease: motion only for the armed step, a StopMove on its deadline, a stop, or the loss of its connection
or the control connection, and the failed StopMove the disarm reports (skills/docs/skills.md). The service as a
process is tested in tests/integration/test_robot_service.py."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from multiprocessing.connection import Client, Listener
from typing import NamedTuple, cast

import pytest

from skills import ipc, robot_ipc, robot_service, state_store
from skills.robot_ipc import LinkEvent, Refused, RobotClient, StateReply
from skills.schema import RobotState
from skills.state_store import StateStore
from skills.tests.fake_clock import Clock


def state(height: float) -> RobotState:
    return RobotState(t=0.0, backend="stub", posture="standing", body_height=height)


class Recorder:
    """A sport client that records its calls; StopMove returns (or raises) the next of ``stops``, then 0, after
    ``stop_delay_s``; every other call returns 0."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.stops: list[int | Exception] = []
        self.stop_delay_s = 0.0

    def Move(self, vx: float, vy: float, vyaw: float) -> int:
        self.calls.append("Move")
        return 0

    def StopMove(self) -> int:
        self.calls.append("StopMove")
        time.sleep(self.stop_delay_s)
        ret = self.stops.pop(0) if self.stops else 0
        if isinstance(ret, Exception):
            raise ret
        return ret

    def StandDown(self) -> int:
        self.calls.append("StandDown")
        return 0

    def Stretch(self) -> int:
        self.calls.append("Stretch")
        return 0


class Served(NamedTuple):
    path: str
    store: StateStore
    robot: Recorder


@contextmanager
def serving(path: str, store: StateStore) -> Iterator[Served]:
    """``store`` and a Motion over a Recorder, served on a socket by robot_service.serve; the motion clock is
    real."""
    robot = Recorder()
    motion = robot_service.Motion(robot, lambda s, abort: abort.wait(s), store, sim=False)
    motion.start()
    listener = Listener(path, family=ipc.FAMILY)

    def accept() -> None:
        while True:
            try:
                conn = listener.accept()
            except OSError:
                return
            threading.Thread(target=robot_service.serve, args=(conn, store, "stub", motion), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    try:
        yield Served(path, store, robot)
    finally:
        listener.close()


@pytest.fixture
def service(tmp_path):
    with serving(str(tmp_path / "r.sock"), StateStore(lambda msg: msg)) as served:
        yield served


def put_later(store: StateStore, msg: RobotState, delay_s: float) -> None:
    threading.Timer(delay_s, store.put, args=(msg,)).start()


def test_hello_and_state_reads(tmp_path):
    """The store clock moves only when advanced: the message is exactly LINK_GAP_S old, the link still up."""
    clock = Clock()
    with serving(str(tmp_path / "r.sock"), StateStore(lambda msg: msg, clock=clock)) as (path, store, _):
        with RobotClient.connect(path) as client:
            assert client.hello["backend"] == "stub"
            empty = client.state()
            assert (empty.state, empty.rx_mono, empty.link) == (None, None, "down")
            store.put(state(0.30))
            clock.advance(state_store.LINK_GAP_S)
            latest = client.state()
            assert latest.state is not None and latest.link == "up"
            assert (latest.seq, latest.state.body_height) == (1, 0.30)
            assert latest.state.age_ms == pytest.approx(state_store.LINK_GAP_S * 1000.0)


def test_read_next_waits_for_a_newer_message(service):
    """min_seq = latest seq + 1: the first message after the request, as decided for comparability."""
    path, store, _ = service
    store.put(state(0.30))
    with RobotClient.connect(path) as client:
        put_later(store, state(0.31), 0.1)
        fresh = client.read_next(wait_s=1.0)
        assert (fresh.seq, fresh.state and fresh.state.body_height) == (2, 0.31)
        t = time.monotonic()
        stale = client.read_next(wait_s=0.1)  # nothing newer comes: the latest earlier message
        assert time.monotonic() - t >= 0.1
        assert stale.seq == 2


def test_stream_sends_new_samples_and_link_changes(tmp_path):
    """The store clock moves only when advanced, so the link changes exactly where the test says."""
    clock = Clock()
    with serving(str(tmp_path / "r.sock"), StateStore(lambda msg: msg, clock=clock)) as (path, store, _):
        store.put(state(0.30))
        with RobotClient.connect(path) as client:
            assert client.subscribe(0.02) == "up"
            first = client.next_event(1.0)
            assert isinstance(first, StateReply) and first.seq == 1
            clock.advance(state_store.LINK_GAP_S + 0.1)  # no new message: no sample, then the gap
            down = client.next_event(1.0)
            assert isinstance(down, LinkEvent) and down.link == "down" and down.rx_mono == first.rx_mono
            store.put(state(0.31))
            events = [client.next_event(1.0), client.next_event(1.0)]  # one stream tick may send them either way
            ups = [e for e in events if isinstance(e, LinkEvent)]
            samples = [e for e in events if isinstance(e, StateReply)]
            assert [e.link for e in ups] == ["up"] and [e.seq for e in samples] == [2]


def test_version_mismatch_is_refused_both_ways(service, tmp_path):
    path, _, _ = service
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


# --- lease ------------------------------------------------------------------------------


def until(check: Callable[[], bool], wait_s: float = 2.0) -> bool:
    deadline = time.monotonic() + wait_s
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def moving_in_thread(path: str, token: str, outcome: list) -> threading.Thread:
    """A skill connection walking for 10 s in a thread; ``outcome`` gets the reply or the exception."""

    def walk() -> None:
        with RobotClient.connect(path) as skill:
            try:
                outcome.append(skill.move(0.3, 0.0, 0.0, 10.0, token=token))
            except Exception as e:
                outcome.append(e)

    thread = threading.Thread(target=walk, daemon=True)
    thread.start()
    return thread


def stopped_after_moving(robot: Recorder) -> bool:
    """The robot moved, the last call is StopMove and no Move followed the first StopMove."""
    calls = list(robot.calls)
    return "Move" in calls and calls[-1] == "StopMove" and "Move" not in calls[calls.index("StopMove") :]


def test_motion_needs_the_armed_lease_on_one_connection(service):
    path, _, robot = service
    with RobotClient.connect(path) as skill, RobotClient.connect(path, control=True) as control:
        with pytest.raises(Refused, match="no lease is armed"):
            skill.action("StandDown", token="t")
        with pytest.raises(Refused, match="busy"):
            RobotClient.connect(path, control=True)
        control.arm("t", 5.0)
        with pytest.raises(Refused, match="token does not match"):
            skill.action("StandDown", token="other")
        assert skill.action("StandDown", token="t") == 0
        with RobotClient.connect(path) as intruder, pytest.raises(Refused, match="another connection"):
            intruder.action("StandDown", token="t")
        with pytest.raises(robot_ipc.ServiceUnavailable, match="bad_request"):
            skill.arm("t2", 5.0)  # only the control connection arms
    assert robot.calls[0] == "StandDown"


def test_deadline_ends_the_lease_and_stops():
    """The motion clock moves only when advanced; the test runs the deadline check itself, so the lease holds just
    before the deadline and ends at it, whatever the machine load."""
    clock, robot = Clock(), Recorder()
    motion = robot_service.Motion(
        robot, lambda s, abort: abort.wait(s), StateStore(lambda m: m), sim=False, clock=clock
    )
    threading.Thread(target=motion._loop, daemon=True).start()
    conn = cast(ipc.Conn, object())  # a lease binds to its connection by identity
    motion.arm("t", 0.3)
    job = motion.submit(conn, "t", motion.move(0.3, 0.0, 0.0, 10.0, None, None))
    assert until(lambda: "Move" in robot.calls, wait_s=10.0)
    clock.advance(0.299)
    assert not motion.end_expired_lease()
    clock.advance(0.002)
    with pytest.raises(robot_service.Refusal, match="deadline has passed"):
        motion.submit(conn, "t", motion.action("StandDown", None))
    assert motion.end_expired_lease()
    assert job.done.wait(5.0) and job.reply is not None
    assert job.reply["error"]["code"] == robot_ipc.ERR_ABORTED and "lease deadline" in job.reply["error"]["message"]
    assert stopped_after_moving(robot)


def test_stop_op_stops_and_ends_the_lease(service):
    path, _, robot = service
    outcome: list = []
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 30.0)
        walking = moving_in_thread(path, "t", outcome)
        assert until(lambda: "Move" in robot.calls)
        sent = control.stop(0.5)
        walking.join(2.0)
    assert sent.ret == 0 and sent.stop_call_ms >= 0
    assert isinstance(outcome[0], Refused) and outcome[0].code == robot_ipc.ERR_ABORTED
    assert stopped_after_moving(robot)


def test_closing_the_lease_connection_stops(service):
    path, _, robot = service
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 30.0)
        with Client(path, family=ipc.FAMILY) as skill:  # a skill killed mid-walk: no reply is read
            skill.send_bytes(ipc.encode(robot_ipc.message("move", token="t", vx=0.3, vy=0.0, vyaw=0.0, duration_s=10)))
            assert until(lambda: "Move" in robot.calls)
        assert until(lambda: stopped_after_moving(robot))
        with RobotClient.connect(path) as late, pytest.raises(Refused, match="no lease"):
            late.action("StandDown", token="t")  # the lease ended with its connection


def test_closing_the_control_connection_stops(service):
    """The dispatcher is gone: the robot stops, whatever the lease."""
    path, _, robot = service
    outcome: list = []
    control = RobotClient.connect(path, control=True)
    control.arm("t", 30.0)
    walking = moving_in_thread(path, "t", outcome)
    assert until(lambda: "Move" in robot.calls)
    control.close()
    walking.join(2.0)
    assert isinstance(outcome[0], Refused) and "control connection closed" in outcome[0].message
    assert stopped_after_moving(robot)
    with RobotClient.connect(path, control=True) as control:  # the next dispatcher may connect
        control.ping()


def test_an_action_never_starts_after_a_stop_and_a_stop_during_it_is_resent():
    """The abort check and the action's commit share the lock; a stop that lands during the call is followed by a
    trailing StopMove, so StopMove is the last command."""

    class StopDuringStretch(Recorder):
        def Stretch(self) -> int:
            super().Stretch()
            motion.halt("stop op")  # what a handler thread does while the action waits for its reply
            return 0

    robot = StopDuringStretch()
    motion = robot_service.Motion(robot, lambda s, abort: abort.wait(s), StateStore(lambda m: m), sim=False)
    job = robot_service.Job(run=motion.action("Stretch", None))
    motion._job = job
    assert job.run(job)["error"]["code"] == robot_ipc.ERR_ABORTED
    assert robot.calls == ["Stretch", "StopMove", "StopMove"]
    late = robot_service.Job(run=motion.action("StandDown", None))
    motion._job = late
    motion.halt("stop op")
    assert late.run(late)["error"]["code"] == robot_ipc.ERR_ABORTED
    assert "StandDown" not in robot.calls


# --- the failed StopMove the disarm reports -----------------------------------------------


def test_disarm_reports_the_failed_stop_ending_a_move(service):
    """The move's closing StopMove and the lease-end StopMove after it both fail: the disarm reports the latest."""
    path, _, robot = service
    robot.stops = [5, 6]
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 30.0)
        with RobotClient.connect(path) as skill:
            assert skill.move(0.3, 0.0, 0.0, 0.2, token="t").stop_ret == 5
        failed = control.disarm()
        assert failed is not None and failed.ret == 6 and failed.stop_call_ms >= 0
        assert failed.reason in ("lease connection closed", "disarmed")  # whichever ended the lease first
        control.arm("t2", 30.0)  # a new lease starts with no record
        assert control.disarm() is None


def test_a_later_successful_stop_clears_the_record(service):
    path, _, robot = service
    robot.stops = [RuntimeError("sdk")]
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 30.0)
        with RobotClient.connect(path) as skill:
            assert skill.move(0.3, 0.0, 0.0, 0.2, token="t").stop_ret is None
        assert control.disarm() is None  # the lease-end StopMove returned 0: the robot is stopped


def test_disarm_reports_the_failed_stop_at_the_deadline(service):
    path, _, robot = service
    outcome: list = []
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 0.3)
        robot.stops = [7]
        moving_in_thread(path, "t", outcome).join(5.0)
        failed = control.disarm()
    assert isinstance(outcome[0], Refused) and outcome[0].code == robot_ipc.ERR_ABORTED
    assert failed is not None and (failed.reason, failed.ret) == ("lease deadline", 7)


def test_disarm_waits_for_the_stop_of_a_closed_lease_connection(service):
    """The skill died and the service's StopMove is still running when the disarm arrives: it waits for it."""
    path, _, robot = service
    robot.stops, robot.stop_delay_s = [8], 0.3
    with RobotClient.connect(path, control=True) as control:
        control.arm("t", 30.0)
        with Client(path, family=ipc.FAMILY) as skill:  # a skill killed mid-walk: no reply is read
            skill.send_bytes(ipc.encode(robot_ipc.message("move", token="t", vx=0.3, vy=0.0, vyaw=0.0, duration_s=10)))
            assert until(lambda: "Move" in robot.calls)
        assert until(lambda: "StopMove" in robot.calls)
        failed = control.disarm()
    assert failed is not None and (failed.reason, failed.ret) == ("lease connection closed", 8)
    assert failed.stop_call_ms >= 300.0
