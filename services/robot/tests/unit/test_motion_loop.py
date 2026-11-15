"""The robot service's motion loop in process with a fake sport client: its move loop and actions
(services/robot/docs/service.md, docs/safety.md). The stub's faults only fail the first call, so the mid-loop and
cleanup paths are covered here. The lease is tested in test_robot_service.py."""

from __future__ import annotations

import math
import threading

import pytest

from contract.schema import RobotState
from services.robot import protocol, service
from services.robot.protocol import MoveTarget
from services.robot.state_store import StateStore
from services.robot.tests.fake_sport import FakeClient, NoArmHook
from tests.helpers.fake_clock import Clock


def run_job(client: FakeClient, run, wait=lambda s, abort: None, store: StateStore | None = None) -> dict:
    """Run one primitive on the service's motion loop, by default with an instant clock; returns its reply."""
    m = service.Motion(client, wait, store or StateStore(lambda msg: msg), NoArmHook())
    threading.Thread(target=m._loop, daemon=True).start()
    job = service.Job(run=run(m))
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
    assert reply["error"] == {"code": protocol.ERR_SDK_EXCEPTION, "message": "ValueError: dds down"}
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
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target), turning(store, 3.0, 0.1), store)
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (1.0, 0, 0)
    assert reply["measured"]["yaw_rad"] == pytest.approx(1.0)
    assert client.calls == ["Move"] * 10 + ["StopMove"]


def test_yaw_target_cap_reached_first_reports_the_shortfall():
    client, store = FakeClient(), StateStore(lambda msg: msg)
    target = MoveTarget(yaw_rad=-1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, -1.0, 0.5, target), turning(store, 0.0, -0.05), store)
    assert (reply["duration_s"], reply["move_ret"], reply["stop_ret"]) == (0.5, 0, 0)
    assert reply["measured"]["yaw_rad"] == pytest.approx(-0.25)


def test_yaw_target_without_yaw_at_the_start_runs_open_loop():
    """No state at all: |target| / |vyaw| + lead_s = 1.0 s + 0.2 s, still under the cap, and no measured change."""
    client = FakeClient()
    target = MoveTarget(yaw_rad=1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target))
    assert (reply["duration_s"], reply["stop_ret"], reply["measured"]) == (1.2, 0, {"yaw_rad": None})
    assert client.calls == ["Move"] * 12 + ["StopMove"]


def test_yaw_target_finishes_on_time_when_yaw_goes_stale():
    """Yaw freezes after 0.3 rad: the remaining 0.7 rad at 1 rad/s runs on time, without the lead. The store
    clock moves only when a tick advances it, so the state is fresh or stale exactly where the test says."""
    clock = Clock()
    client, store = FakeClient(), StateStore(lambda msg: msg, clock=clock)
    live = turning(store, 0.0, 0.1)
    ticks = [0]

    def wait(seconds, abort):
        ticks[0] += 1
        if ticks[0] <= 3:
            live(seconds, abort)  # a new message each tick: age 0
        else:
            clock.advance(service.MAX_STATE_AGE_S + 0.1)  # no new message: the last one ages past the limit

    target = MoveTarget(yaw_rad=1.0, lead_s=0.2)
    reply = run_job(client, lambda m: m.move(0, 0, 1.0, 5.0, target), wait, store)
    assert (reply["duration_s"], reply["stop_ret"], reply["measured"]) == (1.1, 0, {"yaw_rad": None})


def test_action_returns_its_code():
    client = FakeClient(stand_down=3)
    assert run_job(client, lambda m: m.action("StandDown"))["ret"] == 3
    assert client.calls == ["StandDown"]
