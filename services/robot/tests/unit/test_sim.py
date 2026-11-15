"""The simulated sport client, its odometry, obstacle distances and robot faults
(services/robot/docs/service.md)."""

from __future__ import annotations

import json
import math
import threading
import time
from typing import cast

import pytest

from contract import wire
from contract.env import STUB_FAULT, STUB_OBJECTS, STUB_STATE_FILE, STUB_TIME_SCALE
from contract.faults import STUB_ERR_INJECTED, BadFaultFile, RobotFault, fault_file, write_fault
from contract.schema import ObstacleDistances
from services.backend import stub_state
from services.robot import protocol, service
from services.robot.backends import faults, real
from services.robot.backends import sim as sim_backend
from services.robot.backends.faults import SDK_ERR_API_TIMEOUT, SDK_ERR_CLIENT_SEND, FaultFileArm, FaultySportClient
from services.robot.state_store import StateStore
from tests.helpers.fake_clock import Clock

WALK_MPS = 0.3  # a walking speed for the move primitive


def until(check, wait_s: float = 5.0) -> bool:
    deadline = time.monotonic() + wait_s
    while not check():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A stub with its state file in tmp_path, no time scale, no fault and no running command."""
    path = tmp_path / "logs" / "stub_state.json"
    path.parent.mkdir()
    monkeypatch.setenv(STUB_STATE_FILE, str(path))
    monkeypatch.setenv(STUB_TIME_SCALE, "0")
    monkeypatch.delenv(STUB_FAULT, raising=False)
    return path


def test_advance_body_frame():
    p = sim_backend.advance(stub_state.Pose(0.0, 0.0, math.pi / 2), (0.5, 0.0, 0.0), 2.0)  # forward while facing +y
    assert (p.x, p.y) == pytest.approx((0.0, 1.0))
    p = sim_backend.advance(stub_state.Pose(), (0.0, 0.3, 0.0), 1.0)  # +vy is left
    assert (p.x, p.y) == pytest.approx((0.0, 0.3))
    p = sim_backend.advance(stub_state.Pose(), (0.0, 0.0, 1.0), 1.5 * math.pi)  # +vyaw is counter-clockwise, wrapped
    assert (p.x, p.y, p.yaw) == pytest.approx((0.0, 0.0, -math.pi / 2))
    p = sim_backend.advance(stub_state.Pose(), (1.0, 0.0, 1.0), math.pi / 2)  # quarter circle of radius 1
    assert (p.x, p.y, p.yaw) == pytest.approx((1.0, 1.0, math.pi / 2))


def test_move_wait_stop_integrates_and_samples_pose(state):
    sim, abort = sim_backend.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 0.0) == 0
    sim.wait(2.0 + sim_backend.LINEAR_DEAD_S, abort)  # unscaled seconds drive the pose, after the dead time
    moving = sim_backend.sample_state()
    assert moving.velocity == pytest.approx([0.5, 0.0, 0.0])
    assert sim_backend.SimRobot().StopMove() == 0  # another process's client: the command lives in the state file
    sim.wait(5.0, abort)
    at_rest = sim_backend.sample_state()
    assert at_rest.position == pytest.approx([1.0, 0.0, sim_backend.BODY_HEIGHT_STANDING_M])
    assert (at_rest.velocity, at_rest.yaw_speed, at_rest.imu_rpy) == ([0.0, 0.0, 0.0], 0.0, [0.0, 0.0, 0.0])
    stub_state.write_pose(stub_state.Pose(0.0, 0.0, math.pi / 2), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0
    assert sim_backend.sample_state().velocity == pytest.approx([0.0, 0.5, 0.0])  # body frame to world frame


def test_dead_time_per_axis_restarts_only_after_stop_move(state):
    sim, abort = sim_backend.SimRobot(), threading.Event()
    assert sim.Move(0.5, 0.0, 1.0) == 0
    sim.wait(sim_backend.YAW_DEAD_S, abort)
    assert stub_state.read_state(state).pose == stub_state.Pose()  # no axis has started
    sim.wait(sim_backend.LINEAR_DEAD_S - sim_backend.YAW_DEAD_S, abort)  # yaw turns in place meanwhile
    pose = stub_state.read_state(state).pose
    assert (pose.x, pose.y, pose.yaw) == pytest.approx((0.0, 0.0, sim_backend.LINEAR_DEAD_S - sim_backend.YAW_DEAD_S))
    stub_state.write_pose(stub_state.Pose(), state)
    assert sim.Move(0.5, 0.0, 0.0) == 0  # a new command within the sequence keeps its progress
    sim.wait(1.0, abort)
    assert stub_state.read_state(state).pose.x == pytest.approx(0.5)
    assert sim.StopMove() == 0 and sim.Move(0.5, 0.0, 0.0) == 0  # StopMove ends the sequence
    sim.wait(sim_backend.LINEAR_DEAD_S, abort)
    assert stub_state.read_state(state).pose.x == pytest.approx(0.5)


def run_move(
    vx: float, vy: float, vyaw: float, duration_s: float, target=None, *, publish: bool, fault: RobotFault | None = None
) -> dict:
    """One move primitive on the robot service's loop over SimRobot, wrapped and armed with ``fault`` as the sim
    backend does; ``publish``: a state after every tick, as the sim service does, else no state at all."""
    sim, store = sim_backend.SimRobot(), StateStore(lambda msg: msg, clock=Clock())
    client = FaultySportClient(sim)

    def wait(seconds, abort):
        sim.wait(seconds, abort)
        if publish:
            store.put(sim_backend.sample_state())

    if publish:
        store.put(sim_backend.sample_state())
    m = service.Motion(client, wait, store, FaultFileArm(client, sim.path))
    if fault is not None:
        write_fault(sim.path, fault)
    m.arm("token", 10.0)
    threading.Thread(target=m._loop, daemon=True).start()
    job = service.Job(run=m.move(vx, vy, vyaw, duration_s, target))
    m._jobs.put(job)
    assert job.done.wait(5) and job.reply is not None
    return job.reply


def test_move_while_sitting_stores_no_command(state):
    stub_state.reset("sitting", state)
    sim = sim_backend.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == sim_backend.STUB_ERR_NOT_STANDING
    sim.wait(2.0, threading.Event())
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())


def test_stand_down_clears_the_command(state):
    sim = sim_backend.SimRobot()
    assert sim.Move(0.5, 0.0, 0.0) == 0 and sim.StandDown() == 0
    assert stub_state.read_state(state) == stub_state.StubState("sitting", stub_state.Pose())
    assert sim.Stretch() == sim_backend.STUB_ERR_NOT_STANDING


def test_obstacle_ray_cast_follows_the_pose(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 2.0, "y": 0.0}, "person": {"x": 0.0, "y": 9.0}}))
    near = 2.0 - sim_backend.OBSTACLE_RADIUS_M
    ahead = sim_backend.sample_state().obstacle_m
    assert ahead is not None and ahead.front == pytest.approx(near)  # person beyond the max
    assert ahead == ObstacleDistances(front=ahead.front)
    stub_state.write_pose(stub_state.Pose(0.0, 0.0, math.pi / 2), state)  # after a left turn the chair is on the right
    turned = sim_backend.sample_state().obstacle_m
    assert turned is not None and turned.right == pytest.approx(near)
    assert turned == ObstacleDistances(right=turned.right)


def test_obstacle_inside_a_disc_is_zero_in_every_direction(state, monkeypatch):
    monkeypatch.setenv(STUB_OBJECTS, json.dumps({"chair": {"x": 0.1, "y": 0.0}}))  # centre behind the left/right axis
    assert sim_backend.sample_state().obstacle_m == ObstacleDistances(front=0.0, back=0.0, left=0.0, right=0.0)


# --- state fields and robot faults (services/robot/docs/service.md#fault-injection) ------------------------------


def test_nominal_samples_match_the_robot_and_the_posture_rule(state):
    for posture in stub_state.STUB_POSTURES:
        stub_state.reset(posture, state)
        s = sim_backend.sample_state()
        assert s.posture == posture == real.derive_posture(s.body_height, s.mode)
        assert (s.imu_rpy, s.foot_force, s.gait_type, s.error_code) == ([0.0] * 3, [0.0] * 4, 0, sim_backend.ERROR_CODE)
    assert sim_backend.sample_state().mode == sim_backend.MODE_SITTING
    stub_state.reset("standing", state)
    assert sim_backend.sample_state().mode == sim_backend.MODE_STANDING == 0  # measured on the robot


def test_fallen_tilts_the_robot_and_lasts_until_reset(state):
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="fallen")
    assert (reply["move_ret"], reply["stop_ret"]) == (0, 0)  # Move is sent without a reply: nothing tells
    s = sim_backend.sample_state()
    assert s.imu_rpy == pytest.approx([sim_backend.FALLEN_ROLL_RAD, 0.0, 0.0]) and s.mode == sim_backend.MODE_FALLEN
    assert s.posture == real.derive_posture(s.body_height, s.mode)
    assert (s.position, s.velocity) == ([0.0, 0.0, sim_backend.FALLEN_BODY_HEIGHT_M], [0.0] * 3)
    sim = sim_backend.SimRobot()
    assert (sim.Move(0.3, 0.0, 0.0), sim.StandDown(), sim.Stretch()) == (0, 0, 0)
    sim.wait(5.0, threading.Event())
    assert stub_state.read_state(state) == stub_state.StubState(
        condition="fallen"
    )  # no command, no motion, no posture change
    stub_state.reset("standing", state)
    assert sim_backend.sample_state().imu_rpy == [0.0] * 3


def test_stuck_holds_the_pose_until_the_sequence_ends(state):
    sim_backend.SimRobot().inject("stuck")
    assert stub_state.read_state(state).condition is None  # nothing to hold without a running command
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="stuck")
    assert (reply["move_ret"], reply["stop_ret"], reply["duration_s"]) == (0, 0, 3.0)  # the walk reports success
    assert (
        stub_state.read_state(state) == stub_state.StubState()
    )  # still at the origin; the closing StopMove ended stuck
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False)
    assert stub_state.read_state(state).pose.x == pytest.approx(WALK_MPS * (3.0 - sim_backend.LINEAR_DEAD_S))


def test_rejected_fails_every_move_after_the_first_with_the_sdk_code(state):
    reply = run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="rejected")
    assert (reply["move_ret"], reply["stop_ret"]) == (SDK_ERR_CLIENT_SEND, 0)  # the stop still goes through
    assert reply["duration_s"] == protocol.CMD_PERIOD_S and stub_state.read_state(state).command is None


def test_a_re_arm_ends_the_old_lease_clean_and_never_fires_the_new_fault_on_it(state):
    """As the dispatcher's next step: a walk under stop_error is re-armed with rejected while it runs; the re-arm's
    StopMove goes through, the old walk is aborted, and the new lease starts with rejected pending."""
    sim, store = sim_backend.SimRobot(), StateStore(lambda msg: msg)
    client = FaultySportClient(sim)
    m = service.Motion(client, lambda s, abort: abort.wait(0.01), store, FaultFileArm(client, sim.path))
    m.start()
    conn = cast(wire.Conn, object())
    write_fault(state, "stop_error")
    m.arm("t1", 10.0)
    job = m.submit(conn, "t1", m.move(WALK_MPS, 0.0, 0.0, 60.0, None))
    assert until(lambda: stub_state.read_state(state).command is not None)
    write_fault(state, "rejected")
    m.arm("t2", 10.0)
    assert job.done.wait(5) and job.reply is not None and job.reply["error"]["code"] == protocol.ERR_ABORTED
    assert stub_state.read_state(state).command is None and m.disarm() is None  # the re-arm's StopMove returned 0
    m.arm("t3", 10.0)  # never fired on the old walk: the next lease has no fault (t2's was dropped)
    reply = m.action("StandDown")(service.Job(run=lambda j: {}))
    assert reply["ret"] == 0 and stub_state.read_posture(state) == "sitting"


def test_stuck_on_one_step_does_not_fault_the_next(state):
    run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False, fault="stuck")
    assert stub_state.read_state(state).pose.x == 0.0
    run_move(WALK_MPS, 0.0, 0.0, 3.0, publish=False)
    assert stub_state.read_state(state).pose.x == pytest.approx(WALK_MPS * (3.0 - sim_backend.LINEAR_DEAD_S))


# --- the fault wrapper (services/robot/docs/service.md#fault-injection) -------------------------------------------


def test_a_fault_fires_once_after_the_first_accepted_move(state):
    """Mid-walk: the first Move's command is set when the fault fires; a refused first Move consumes it unfired."""
    client = FaultySportClient(sim_backend.SimRobot())
    client.arm("fallen")
    stub_state.write_posture("sitting", state)
    assert client.Move(WALK_MPS, 0.0, 0.0) == sim_backend.STUB_ERR_NOT_STANDING
    stub_state.write_posture("standing", state)
    assert client.Move(WALK_MPS, 0.0, 0.0) == 0 and stub_state.read_state(state).condition is None
    client.arm("stuck")
    assert client.Move(WALK_MPS, 0.0, 0.0) == 0
    assert stub_state.read_state(state).condition == "stuck"  # the command was set, so stuck holds it
    assert client.StopMove() == 0 and client.Move(WALK_MPS, 0.0, 0.0) == 0
    assert stub_state.read_state(state).condition is None  # a new sequence, not faulted again


def test_rejected_and_stop_error_stick_until_the_next_arm(state):
    """Actions fire the fault before the call; the sticky faults never reach the sim robot."""
    client = FaultySportClient(sim_backend.SimRobot())
    client.arm("rejected")
    assert (client.StandDown(), client.Stretch(), client.Move(WALK_MPS, 0.0, 0.0)) == (
        SDK_ERR_API_TIMEOUT,
        SDK_ERR_API_TIMEOUT,
        SDK_ERR_CLIENT_SEND,
    )
    assert stub_state.read_state(state) == stub_state.StubState()
    client.arm("stop_error")
    assert client.Move(WALK_MPS, 0.0, 0.0) == 0
    assert (client.StopMove(), client.StopMove()) == (STUB_ERR_INJECTED, STUB_ERR_INJECTED)
    assert stub_state.read_state(state).command is not None  # the robot keeps walking
    client.arm(None)
    assert (client.StopMove(), client.StandDown()) == (0, 0)
    assert stub_state.read_state(state).posture == "sitting"


@pytest.mark.parametrize("kind, call", [("service_crash", "Move"), ("service_hang", "Stretch")])
def test_service_faults_act_on_the_process(state, monkeypatch, kind, call):
    fired: list[str] = []
    monkeypatch.setattr(faults, "apply_process_fault", fired.append)
    client = FaultySportClient(sim_backend.SimRobot())
    client.arm(kind)
    args = (WALK_MPS, 0.0, 0.0) if call == "Move" else ()
    assert getattr(client, call)(*args) == 0 and fired == [kind]
    assert getattr(client, call)(*args) == 0 and fired == [kind]


def test_sim_backend_arms_from_the_fault_file(state):
    """The service start drops a stale fault file; each arm takes the file's fault; a bad file refuses the arm."""
    write_fault(state, "rejected")
    client, _wait, hook = sim_backend.SimBackend().motion(StateStore(lambda msg: msg))
    hook.arm()
    assert client.StandDown() == 0
    write_fault(state, "rejected")
    hook.arm()
    assert not fault_file(state).exists() and client.Stretch() == SDK_ERR_API_TIMEOUT
    hook.arm()
    assert client.Stretch() == sim_backend.STUB_ERR_NOT_STANDING
    fault_file(state).write_text("{}")
    with pytest.raises(BadFaultFile):
        hook.arm()
    write_fault(state, "stuck")
    stub_state.reset("standing", state)
    assert not fault_file(state).exists()
