"""The sim backend (robot/docs/service.md): the simulated sport client (SimRobot, run by the robot service and the
stop_move utility) over the stub state file, its odometry, obstacle distances and state sample."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from contract.backend import stub_sleep, time_scale
from contract.schema import ObstacleDistances, Posture, RobotState
from robot import ramp
from robot.faults import Condition
from robot.stub_state import (
    Command,
    Pose,
    WorldObject,
    objects,
    read_state,
    state_file,
    write_command,
    write_state,
)

STUB_ERR_NOT_STANDING = 1

STAND_DOWN_S = 1.5
STRETCH_S = 3.0
BODY_HEIGHT_STANDING_M = 0.32
BODY_HEIGHT_SITTING_M = 0.08

# Nominal state fields (robot/docs/robot.md): standing values as measured on the robot, the rest unverified.
MODE_STANDING = 0
MODE_SITTING = 5  # lieDown in Unitree's sport mode list, unverified
GAIT_TYPE = 0
ERROR_CODE = 100  # reported in every standing sample on the robot; meaning undocumented
FOOT_FORCE = (0.0, 0.0, 0.0, 0.0)  # rt/sportmodestate reports zeros on the robot
# Fallen condition (robot/docs/service.md): on its side, the sport mode damped; all unverified (tunable).
FALLEN_ROLL_RAD = math.pi / 2
FALLEN_BODY_HEIGHT_M = 0.10
MODE_FALLEN = 7  # damping in Unitree's sport mode list
# The rejected fault's codes, the Unitree SDK's (unitree_sdk2py/rpc/internal.py): Move is sent without a reply, so a
# failed send is the only non-zero code it returns; actions wait for a reply and time out.
SDK_ERR_CLIENT_SEND = 3102
SDK_ERR_API_TIMEOUT = 3104

# Simulated range_obstacle (robot/docs/service.md): each object is a disc, rays leave the body centre.
OBSTACLE_RADIUS_M = 0.15  # (tunable)
OBSTACLE_MAX_M = 5.0  # nothing hit within this range -> None (tunable)
OBSTACLE_DIRECTIONS = {"front": 0.0, "back": math.pi, "left": math.pi / 2, "right": -math.pi / 2}  # body frame
# Start ramp as dead time per axis (robot/docs/service.md): the robot's calibrated ramp, which the skills' LEAD_S cover.
LINEAR_DEAD_S = ramp.LINEAR_S
YAW_DEAD_S = ramp.YAW_S


# --- odometry ---------------------------------------------------------------------


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def advance(pose: Pose, command: tuple[float, float, float], seconds: float) -> Pose:
    """The pose after holding body-frame velocity (vx, vy, vyaw) for ``seconds``, integrated exactly."""
    vx, vy, vyaw = command
    yaw1 = pose.yaw + vyaw * seconds
    if vyaw == 0:
        s, c = seconds * math.cos(pose.yaw), seconds * math.sin(pose.yaw)
    else:  # integrals of cos(yaw) and sin(yaw) over the arc
        s = (math.sin(yaw1) - math.sin(pose.yaw)) / vyaw
        c = (math.cos(pose.yaw) - math.cos(yaw1)) / vyaw
    return Pose(x=pose.x + vx * s - vy * c, y=pose.y + vx * c + vy * s, yaw=_wrap(yaw1))


def advance_sequence(pose: Pose, command: Command, start_s: float, seconds: float) -> Pose:
    """The pose after ``seconds`` more of a Move sequence already ``start_s`` old: each axis moves only once the
    sequence is past its dead time."""
    vx, vy, vyaw = command
    end = start_s + seconds
    cuts = sorted({start_s, end, *(min(max(d, start_s), end) for d in (LINEAR_DEAD_S, YAW_DEAD_S))})
    for t0, t1 in zip(cuts, cuts[1:], strict=False):
        linear, yaw = t0 >= LINEAR_DEAD_S, t0 >= YAW_DEAD_S
        pose = advance(pose, (vx if linear else 0.0, vy if linear else 0.0, vyaw if yaw else 0.0), t1 - t0)
    return pose


def _ray_distance(pose: Pose, angle: float, world: Mapping[str, WorldObject]) -> float | None:
    """Distance from the body centre along world ``angle`` to the nearest object disc, None beyond the max."""
    best: float | None = None
    for obj in world.values():
        dx, dy = obj.x - pose.x, obj.y - pose.y
        along = dx * math.cos(angle) + dy * math.sin(angle)
        across = -dx * math.sin(angle) + dy * math.cos(angle)
        if math.hypot(dx, dy) <= OBSTACLE_RADIUS_M:
            hit = 0.0  # the ray starts inside the disc
        elif along <= 0 or abs(across) > OBSTACLE_RADIUS_M:
            continue
        else:
            hit = along - math.sqrt(OBSTACLE_RADIUS_M**2 - across**2)
        best = hit if best is None else min(best, hit)
    return best if best is not None and best <= OBSTACLE_MAX_M else None


def obstacle_distances(pose: Pose, world: Mapping[str, WorldObject]) -> ObstacleDistances:
    return ObstacleDistances(**{k: _ray_distance(pose, pose.yaw + a, world) for k, a in OBSTACLE_DIRECTIONS.items()})


# --- simulated sport client --------------------------------------------------------


class SimRobot:
    """The stub's sport client, over the state file. Every call returns int, 0 = success. ``wait`` is the robot
    service's motion clock: it waits the scaled time and advances the pose by the unscaled seconds while a
    command is set, after each axis's dead time from the sequence start. Without a wait the pose stays put, but
    the command stays set until StopMove. ``_lock`` serialises this process's read-modify-writes, so a stop from
    another thread is never overwritten."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or state_file()
        self._lock = threading.Lock()

    def Move(self, vx: float, vy: float, vyaw: float) -> int:
        with self._lock:
            state = read_state(self.path)
            if state.condition == "fallen":
                return 0  # sent without a reply, as on the robot; a fallen robot does not move
            if state.posture != "standing":
                return STUB_ERR_NOT_STANDING
            write_command((vx, vy, vyaw), self.path)
        return 0

    def StopMove(self) -> int:
        with self._lock:
            write_command(None, self.path)
        return 0

    def StandDown(self) -> int:
        with self._lock:
            state = read_state(self.path)
            if state.posture != "standing" or state.condition == "fallen":
                return 0
            write_state(replace(state, posture="sitting", command=None, sequence_s=0.0, condition=None), self.path)
        stub_sleep(STAND_DOWN_S)
        return 0

    def Stretch(self) -> int:
        state = read_state(self.path)
        if state.condition == "fallen":
            return 0
        if state.posture != "standing":
            return STUB_ERR_NOT_STANDING
        stub_sleep(STRETCH_S)
        return 0

    def inject(self, condition: Condition) -> None:
        """The robot service's side of a robot fault: fallen drops the command and lasts until reset; stuck holds
        the running Move sequence in place and does nothing without one."""
        with self._lock:
            state = read_state(self.path)
            if condition == "fallen":
                write_state(replace(state, command=None, sequence_s=0.0, condition="fallen"), self.path)
            elif state.command is not None and state.condition is None:
                write_state(replace(state, condition="stuck"), self.path)

    def wait(self, seconds: float, abort: threading.Event) -> None:
        """Wait ``seconds`` scaled, or until ``abort``; a set command moves the pose by the unscaled time waited,
        less what remains of its dead time."""
        scale = time_scale()
        t = time.monotonic()
        aborted = abort.wait(seconds * scale)
        waited = min((time.monotonic() - t) / scale, seconds) if aborted and scale > 0 else seconds
        with self._lock:
            state = read_state(self.path)
            if state.command is not None and state.condition is None:
                pose = advance_sequence(state.pose, state.command, state.sequence_s, waited)
                write_state(replace(state, pose=pose, sequence_s=state.sequence_s + waited), self.path)


def sample_state() -> RobotState:
    """The real state sample's fields with the robot's values for the posture or the fault condition; velocity is
    the running command in the world frame (zero at rest or under a fault); obstacle distances are ray-cast
    against the stub objects."""
    state = read_state()
    pose = state.pose
    posture: Posture = state.posture
    roll = 0.0
    if state.condition == "fallen":
        posture, height, mode, roll = "sitting", FALLEN_BODY_HEIGHT_M, MODE_FALLEN, FALLEN_ROLL_RAD  # by body height
    elif state.posture == "standing":
        height, mode = BODY_HEIGHT_STANDING_M, MODE_STANDING
    else:
        height, mode = BODY_HEIGHT_SITTING_M, MODE_SITTING
    vx, vy, vyaw = (state.command if state.condition is None else None) or (0.0, 0.0, 0.0)
    cos, sin = math.cos(pose.yaw), math.sin(pose.yaw)
    return RobotState(
        t=time.time(),
        backend="stub",
        posture=posture,
        mode=mode,
        gait_type=GAIT_TYPE,
        body_height=height,
        position=[pose.x, pose.y, height],
        velocity=[vx * cos - vy * sin, vx * sin + vy * cos, 0.0],
        yaw_speed=vyaw,
        imu_rpy=[roll, 0.0, pose.yaw],
        foot_force=list(FOOT_FORCE),
        error_code=ERROR_CODE,
        obstacle_m=obstacle_distances(pose, objects()),
    )
