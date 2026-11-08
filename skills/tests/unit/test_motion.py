"""Motion in process with a fake sport client: the robot service's move loop and actions, the skills' mapping of
primitive replies to outcomes, and the stop_move utility (skills/docs/skills.md, docs/safety.md). The stub's
faults only fail the first call, so the mid-loop and cleanup paths are covered here. The lease is tested in
test_robot_service.py."""

from __future__ import annotations

import sys
import threading

import pytest

from skills import backend, local, motion, process, robot_ipc, robot_service, runner, stop_move
from skills.robot_ipc import MoveResult


class FakeClient:
    """Sport client returning scripted codes. ``move`` items: int (returned) or an
    exception instance (raised); after the list runs out Move returns 0."""

    def __init__(self, move=(), stop=0, stand_down=0):
        self.move = list(move)
        self.stop = stop
        self.stand_down = stand_down
        self.calls: list[str] = []

    def Move(self, vx, vy, vyaw):
        self.calls.append("Move")
        item = self.move.pop(0) if self.move else 0
        if isinstance(item, BaseException):
            raise item
        return item

    def StopMove(self):
        self.calls.append("StopMove")
        if isinstance(self.stop, BaseException):
            raise self.stop
        return self.stop

    def StandDown(self):
        self.calls.append("StandDown")
        return self.stand_down

    def Stretch(self):
        self.calls.append("Stretch")
        return 0


def run_job(client: FakeClient, run) -> dict:
    """Run one primitive on the service's motion loop with an instant clock; returns its reply."""
    m = robot_service.Motion(client, lambda s, abort: None, sim=False)
    threading.Thread(target=m._loop, daemon=True).start()
    job = robot_service.Job(run=run(m))
    m._jobs.put(job)
    assert job.done.wait(5)
    assert job.reply is not None
    return job.reply


# --- the service's move loop -----------------------------------------------------------


@pytest.mark.parametrize("stop, stop_ret", [(0, 0), (5, 5), (RuntimeError("x"), None)], ids=["ok", "fails", "raises"])
def test_move_fails_mid_loop_then_stops(stop, stop_ret):
    client = FakeClient(move=[0, 0, 7], stop=stop)
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 1.0, None))
    assert (reply["op"], reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == ("move", 0.2, 7, stop_ret)
    assert client.calls == ["Move"] * 3 + ["StopMove"]


def test_move_runs_its_duration_then_stops():
    client = FakeClient()
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 0.3, None))
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (0.3, 0, 0)
    assert client.calls == ["Move"] * 3 + ["StopMove"]


def test_move_raises_still_stops():
    client = FakeClient(move=[0, ValueError("dds down")])
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 1.0, None))
    assert reply["error"] == {"code": robot_ipc.ERR_SDK_EXCEPTION, "message": "ValueError: dds down"}
    assert client.calls == ["Move", "Move", "StopMove"]


def test_action_returns_its_code():
    client = FakeClient(stand_down=3)
    assert run_job(client, lambda m: m.action("StandDown", None))["ret"] == 3
    assert client.calls == ["StandDown"]


# --- the skills' side ---------------------------------------------------------------------


class FakeSession:
    def __init__(self, reply):
        self.reply = reply
        self.dropped = False

    def client(self):
        return self

    def drop(self):
        self.dropped = True

    def move(self, *args, **kwargs):
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply

    def action(self, *args, **kwargs):
        return self.move()


@pytest.fixture
def session(monkeypatch):
    """Install a FakeSession answering ``reply``; record backend sleeps."""
    sleeps: list[float] = []
    monkeypatch.setenv("GO2_BACKEND", "real")  # no stub faults
    monkeypatch.setattr(backend, "sleep", sleeps.append)

    def install(reply):
        fake = FakeSession(reply)
        monkeypatch.setattr(robot_ipc, "session", fake)
        return fake, sleeps

    return install


@pytest.mark.parametrize(
    "result, code, message, sdk_ret",
    [
        (MoveResult(2.0, 0, 0), None, None, 0),
        (MoveResult(0.2, 7, 5), "sdk_error", "Move returned 7; StopMove returned 5", 7),
        (MoveResult(0.3, 0, 4), "sdk_error", "StopMove returned 4", 4),
    ],
    ids=["ok", "move_fails", "stop_fails"],
)
def test_move_outcome(session, result, code, message, sdk_ret):
    session(result)
    o = motion.move(motion.Velocity(0.3, 0, 0), 2.0)
    assert (o.error_code, o.error_message) == (code, message)
    assert o.observations == {"duration_s": result.duration_s, "sdk_ret": sdk_ret}


@pytest.mark.parametrize(
    "error, code, message",
    [
        (robot_ipc.Refused("no_lease", "no lease is armed"), "motion_rejected", "no_lease: no lease is armed"),
        (robot_ipc.Refused("sdk_exception", "ValueError: x"), "exception", "ValueError: x"),
        (robot_ipc.ServiceUnavailable("gone"), "robot_unavailable", "ServiceUnavailable: gone"),
    ],
    ids=["no_lease", "sdk_exception", "unavailable"],
)
def test_refused_primitive_drops_the_connection(session, error, code, message):
    fake, _ = session(error)
    o = motion.single_action("StandDown", 2.0)
    assert (o.status, o.error_code, o.error_message) == ("error", code, message)
    assert fake.dropped


def test_single_action_settles_only_on_success(session):
    _, sleeps = session(3)
    o = motion.single_action("StandDown", 2.0)
    assert (o.error_code, o.error_message, o.observations, sleeps) == (
        "sdk_error",
        "StandDown returned 3",
        {"sdk_ret": 3},
        [],
    )
    _, sleeps = session(0)
    assert motion.single_action("StandDown", 2.0).status == "ok" and sleeps == [2.0]


# --- stop_move utility -----------------------------------------------------------------


class Emitted(Exception):
    """Raised by the patched runner.emit instead of exiting the process."""


@pytest.fixture
def run_stop_move(monkeypatch):
    """Run stop_move.main() in process with its own fake sport client; returns the SkillResponse it would emit."""

    def run(argv, client, state=None):
        def sample():
            if isinstance(state, BaseException):
                raise state
            return state or {"posture": "standing"}

        monkeypatch.setenv("GO2_BACKEND", "stub")
        monkeypatch.setattr(backend, "sleep", lambda s: None)
        monkeypatch.setattr(local, "init_dds", lambda: None)
        monkeypatch.setattr(local, "sport_client", lambda: client)
        monkeypatch.setattr(local, "sample_state", sample)
        monkeypatch.setattr(local, "setup_timing", lambda: {"dds_init_ms": 1.0})
        monkeypatch.setattr(process, "capture_stdout", lambda: None)
        monkeypatch.setattr(sys, "argv", ["stop_move", *argv])
        emitted = []

        def emit(skill, outcome, states, total_ms, setup):
            emitted.append(runner.build_response(skill, outcome, states, total_ms, setup=setup))
            raise Emitted

        monkeypatch.setattr(runner, "emit", emit)
        with pytest.raises(Emitted):
            stop_move.main()
        return emitted[0]

    return run


def test_stop_move_invalid_params_still_stops(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["not json"], client)
    assert client.calls == ["StopMove"]
    assert out.error is not None and out.error.code == "invalid_params"
    assert out.observations == {"sdk_ret": 0} and list(out.timing) == [
        "dds_init_ms",
        "client_init_ms",
        "stop_call_ms",
        "state_ms",
        "total_ms",
    ]


def test_stop_move_sdk_error(run_stop_move):
    out = run_stop_move(["{}"], FakeClient(stop=9))
    assert out.error is not None and (out.error.code, out.error.message) == ("sdk_error", "StopMove returned 9")


def test_stop_move_state_error_keeps_ok(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["{}"], client, state=backend.StateUnavailable("no message"))
    assert client.calls == ["StopMove"]
    assert (out.status, out.error, out.state_after) == ("ok", None, None)
    assert out.state_error == "after: StateUnavailable: no message"
