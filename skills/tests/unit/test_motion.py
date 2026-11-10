"""Motion in process with a fake sport client: the robot service's move loop and actions, the skills' mapping of
primitive replies to outcomes, and the stop_move utility (skills/docs/skills.md, docs/safety.md). The stub's
faults only fail the first call, so the mid-loop and cleanup paths are covered here. The lease is tested in
test_robot_service.py."""

from __future__ import annotations

import math
import sys
import threading
import time

import pytest

from skills import backend, local, motion, process, robot_ipc, robot_service, runner, stop_move
from skills.robot_ipc import MoveResult, MoveTarget
from skills.schema import RobotState
from skills.state_store import StateStore


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


def run_job(client: FakeClient, run, wait=lambda s, abort: None, store: StateStore | None = None) -> dict:
    """Run one primitive on the service's motion loop, by default with an instant clock; returns its reply."""
    m = robot_service.Motion(client, wait, store or StateStore(lambda msg: msg), sim=False)
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
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 1.0, None, None))
    assert (reply["op"], reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == ("move", 0.2, 7, stop_ret)
    assert client.calls == ["Move"] * 3 + ["StopMove"]


def test_move_runs_its_duration_then_stops():
    client = FakeClient()
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 0.3, None, None))
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (0.3, 0, 0)
    assert client.calls == ["Move"] * 3 + ["StopMove"]


def test_move_raises_still_stops():
    client = FakeClient(move=[0, ValueError("dds down")])
    reply = run_job(client, lambda m: m.move(0.3, 0, 0, 1.0, None, None))
    assert reply["error"] == {"code": robot_ipc.ERR_SDK_EXCEPTION, "message": "ValueError: dds down"}
    assert client.calls == ["Move", "Move", "StopMove"]


def yaw_state(yaw: float) -> RobotState:
    return RobotState(t=0.0, backend="stub", posture="standing", imu_rpy=[0.0, 0.0, yaw])


def turning(store: StateStore, start: float, step: float):
    """A motion clock whose robot turns ``step`` rad per tick from ``start``, publishing yaw wrapped to (-pi, pi]."""
    yaw = [start]
    store.put(yaw_state(start))

    def wait(seconds, abort):
        yaw[0] += step
        store.put(yaw_state(math.remainder(yaw[0], math.tau)))

    return wait


def test_yaw_target_stops_on_the_measured_change_across_pi():
    """Left from 3.0 rad through +-pi: 10 ticks of 0.1 rad pass 1.0 rad minus the margin, well before the cap."""
    client, store = FakeClient(), StateStore(lambda msg: msg)
    target = MoveTarget(yaw_rad=1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target, None), turning(store, 3.0, 0.1), store)
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (1.0, 0, 0)
    assert reply["measured"]["yaw_rad"] == pytest.approx(1.0)
    assert client.calls == ["Move"] * 10 + ["StopMove"]


def test_yaw_target_cap_reached_first_reports_the_shortfall():
    client, store = FakeClient(), StateStore(lambda msg: msg)
    target = MoveTarget(yaw_rad=-1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, -1.0, 0.5, target, None), turning(store, 0.0, -0.05), store)
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (0.5, 0, 0)
    assert reply["measured"]["yaw_rad"] == pytest.approx(-0.25)


def test_yaw_target_without_yaw_at_the_start_runs_open_loop():
    """No state at all: |target| / |vyaw| + lead_s = 1.0 s + 0.2 s, still under the cap, and no measured change."""
    client = FakeClient()
    target = MoveTarget(yaw_rad=1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target, None))
    assert (reply["duration_s"], reply["stop_ret"], reply["measured"]) == (1.2, 0, {"yaw_rad": None})
    assert client.calls == ["Move"] * 12 + ["StopMove"]


def test_yaw_target_finishes_on_time_when_yaw_goes_stale(monkeypatch):
    """Yaw freezes after 0.3 rad: the remaining 0.7 rad at 1 rad/s runs on time, without the lead."""
    monkeypatch.setattr(robot_service, "MAX_STATE_AGE_S", 0.05)
    client, store = FakeClient(), StateStore(lambda msg: msg)
    live = turning(store, 0.0, 0.1)
    ticks = [0]

    def wait(seconds, abort):
        ticks[0] += 1
        if ticks[0] <= 3:
            live(seconds, abort)
        else:
            time.sleep(0.1)  # no new message: the last one ages past the limit

    target = MoveTarget(yaw_rad=1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target, None), wait, store)
    assert (reply["duration_s"], reply["stop_ret"], reply["measured"]) == (1.1, 0, {"yaw_rad": None})


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
    "yaw_rad, extra",
    [(-1.6, {"closed_loop": True, "turned_deg": 91.7}), (None, {"closed_loop": False, "turned_deg": None})],
    ids=["closed_loop", "fell_back"],
)
def test_move_outcome_with_a_yaw_target(session, yaw_rad, extra):
    session(MoveResult(1.6, 0, 0, yaw_rad=yaw_rad))
    o = motion.move(motion.Velocity(0, 0, -1.0), 2.5, MoveTarget(yaw_rad=-1.57, lead_s=0.2))
    assert o.status == "ok" and o.observations == {"duration_s": 1.6, "sdk_ret": 0, **extra}


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
