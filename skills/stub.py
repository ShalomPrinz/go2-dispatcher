"""Stub backend (skills/docs/skills.md): keeps posture, an odometry pose and the running velocity command in a
shared JSON state file, simulates the sport client (SimRobot, run by the robot service and the stop_move utility)
and the front camera over objects placed in the world, and supports fault injection."""

from __future__ import annotations

import json
import math
import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, get_args

from contract.env import STUB_FAULT, STUB_OBJECTS, STUB_STATE_FILE, STUB_TIME_SCALE
from contract.schema import ObstacleDistances, Posture, RobotState
from skills import process, ramp, vision
from skills.backend import DetectResult

# Stub defaults: used when the env is unset (manual runs) and as the dispatcher's stub config defaults.
DEFAULT_STATE_FILE = "logs/.stub_state.json"
DEFAULT_TIME_SCALE = 0.1

StubPosture = Literal["standing", "sitting"]  # the postures the stub can hold; a subset of contract.schema.Posture
STUB_POSTURES: tuple[StubPosture, ...] = get_args(StubPosture)
DEFAULT_POSTURE: StubPosture = "standing"

STUB_ERR_NOT_STANDING = 1
STUB_ERR_INJECTED = 99
FaultKind = Literal[
    "error", "hang", "crash", "garbage", "service_hang", "service_crash", "stop_error", "rejected", "fallen", "stuck"
]
FAULT_KINDS: tuple[FaultKind, ...] = get_args(FaultKind)
Condition = Literal["fallen", "stuck"]  # robot faults the sim robot keeps in the state file
CONDITIONS: tuple[Condition, ...] = get_args(Condition)
# Applied by the robot service; the conditions by the sim robot, the rest by the service itself.
SERVICE_FAULTS: tuple[FaultKind, ...] = ("service_hang", "service_crash", "stop_error", "rejected", *CONDITIONS)
CRASH_EXIT_CODE = 139
GARBAGE_TEXT = "not json\n"
HANG_POLL_S = 1.0

STAND_DOWN_S = 1.5
STRETCH_S = 3.0
DETECT_S = 0.5
BODY_HEIGHT_STANDING_M = 0.32
BODY_HEIGHT_SITTING_M = 0.08

# Nominal state fields (skills/docs/robot.md): standing values as measured on the robot, the rest unverified.
MODE_STANDING = 0
MODE_SITTING = 5  # lieDown in Unitree's sport mode list, unverified
GAIT_TYPE = 0
ERROR_CODE = 100  # reported in every standing sample on the robot; meaning undocumented
FOOT_FORCE = (0.0, 0.0, 0.0, 0.0)  # rt/sportmodestate reports zeros on the robot
# Fallen condition (skills/docs/skills.md): on its side, the sport mode damped; all unverified (tunable).
FALLEN_ROLL_RAD = math.pi / 2
FALLEN_BODY_HEIGHT_M = 0.10
MODE_FALLEN = 7  # damping in Unitree's sport mode list
# The rejected fault's codes, the Unitree SDK's (unitree_sdk2py/rpc/internal.py): Move is sent without a reply, so a
# failed send is the only non-zero code it returns; actions wait for a reply and time out.
SDK_ERR_CLIENT_SEND = 3102
SDK_ERR_API_TIMEOUT = 3104

# Simulated front camera (skills/docs/robot.md): Unitree Go2 spec 1280x720, 120 deg FOV, taken as horizontal.
CAMERA_WIDTH_PX = 1280  # (tunable)
CAMERA_HEIGHT_PX = 720  # (tunable)
CAMERA_HFOV_DEG = 120.0  # (tunable)
CAMERA_OFFSET_M = 0.3  # camera ahead of the body centre (tunable)
CAMERA_FOCAL_PX = CAMERA_WIDTH_PX / 2 / math.tan(math.radians(CAMERA_HFOV_DEG) / 2)  # square pixels
MIN_BOX_H = 0.02  # smaller boxes are not detected (tunable)
CONFIDENCE_MIN = 0.6  # at box height 0 (tunable)
CONFIDENCE_MAX = 0.95  # at box height 1 (tunable)
# Object heights for GO2_STUB_OBJECTS entries without height_m.
DEFAULT_HEIGHTS_M = {"bottle": 0.25, "cup": 0.10, "chair": 0.90, "person": 1.70}
# Simulated range_obstacle (skills/docs/skills.md): each object is a disc, rays leave the body centre.
OBSTACLE_RADIUS_M = 0.15  # (tunable)
OBSTACLE_MAX_M = 5.0  # nothing hit within this range -> None (tunable)
OBSTACLE_DIRECTIONS = {"front": 0.0, "back": math.pi, "left": math.pi / 2, "right": -math.pi / 2}  # body frame
# Start ramp as dead time per axis (skills/docs/skills.md): the robot's calibrated ramp, which the skills' LEAD_S cover.
LINEAR_DEAD_S = ramp.LINEAR_S
YAW_DEAD_S = ramp.YAW_S


@dataclass(frozen=True)
class Pose:
    """Odometry pose in the world frame: metres, yaw in radians counter-clockwise from +x."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


Command = tuple[float, float, float]  # body-frame (vx, vy, vyaw) of the last accepted Move


@dataclass(frozen=True)
class StubState:
    posture: StubPosture = DEFAULT_POSTURE
    pose: Pose = Pose()
    command: Command | None = None  # set by Move, cleared by StopMove: the robot keeps it until then
    sequence_s: float = 0.0  # unscaled time waited since the command was set: the dead time's progress
    condition: Condition | None = None  # an injected robot fault: fallen until reset, stuck until the sequence ends


@dataclass(frozen=True)
class WorldObject:
    x: float
    y: float
    height_m: float


# --- settings -----------------------------------------------------------------


def state_file() -> Path:
    return Path(os.environ.get(STUB_STATE_FILE) or DEFAULT_STATE_FILE)


def time_scale() -> float:
    raw = os.environ.get(STUB_TIME_SCALE)
    return float(raw) if raw else DEFAULT_TIME_SCALE


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def parse_objects(data: object) -> dict[str, WorldObject]:
    """Validate ``{class: {"x", "y", "height_m"?}}``; ``height_m`` defaults from ``DEFAULT_HEIGHTS_M``."""
    if not isinstance(data, Mapping):
        raise ValueError(f"stub objects must be an object of objects, got {data!r}")
    out: dict[str, WorldObject] = {}
    for name, spec in data.items():
        if not isinstance(spec, Mapping) or not {"x", "y"} <= set(spec) <= {"x", "y", "height_m"}:
            raise ValueError(f"stub object {name!r} must have x, y and optional height_m, got {spec!r}")
        if "height_m" in spec:
            height = _finite(spec["height_m"], f"stub object {name!r} height_m")
            if height <= 0:
                raise ValueError(f"stub object {name!r} height_m must be > 0, got {height!r}")
        elif name in DEFAULT_HEIGHTS_M:
            height = DEFAULT_HEIGHTS_M[name]
        else:
            raise ValueError(
                f"stub object {name!r} needs height_m (defaults exist only for {', '.join(DEFAULT_HEIGHTS_M)})"
            )
        out[name] = WorldObject(
            x=_finite(spec["x"], f"stub object {name!r} x"),
            y=_finite(spec["y"], f"stub object {name!r} y"),
            height_m=height,
        )
    return out


def objects() -> dict[str, WorldObject]:
    raw = os.environ.get(STUB_OBJECTS)
    return parse_objects(json.loads(raw)) if raw else {}


def sleep(seconds: float) -> None:
    """Wait ``seconds`` scaled by the time scale."""
    time.sleep(seconds * time_scale())


# --- odometry and camera ----------------------------------------------------------


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


def project(pose: Pose, obj: WorldObject) -> DetectResult:
    """What the front camera at ``pose`` sees of ``obj``: a pinhole box classified like the real detector's."""
    cam_x = pose.x + CAMERA_OFFSET_M * math.cos(pose.yaw)
    cam_y = pose.y + CAMERA_OFFSET_M * math.sin(pose.yaw)
    dx, dy = obj.x - cam_x, obj.y - cam_y
    depth = dx * math.cos(pose.yaw) + dy * math.sin(pose.yaw)
    left = -dx * math.sin(pose.yaw) + dy * math.cos(pose.yaw)
    if depth <= 0 or abs(math.atan2(left, depth)) > math.radians(CAMERA_HFOV_DEG) / 2:
        return DetectResult(found=False)
    h_frac = min(1.0, CAMERA_FOCAL_PX * obj.height_m / depth / CAMERA_HEIGHT_PX)
    if h_frac < MIN_BOX_H:
        return DetectResult(found=False)
    cx_frac = 0.5 - CAMERA_FOCAL_PX * (left / depth) / CAMERA_WIDTH_PX  # positive bearing (left) -> small x
    return vision.classify_box(cx_frac, h_frac, CONFIDENCE_MIN + (CONFIDENCE_MAX - CONFIDENCE_MIN) * h_frac)


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


# --- state file -----------------------------------------------------------------


def read_state(path: Path | None = None) -> StubState:
    """The state file; a missing file means standing at the origin, a missing pose the origin, a missing
    command or condition none and a missing sequence time zero."""
    path = path or state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return StubState()
    try:
        posture = data["posture"]
        if posture not in STUB_POSTURES:
            raise ValueError(posture)
        pose = data.get("pose")
        command = data.get("command")
        condition = data.get("condition")
        if condition is not None and condition not in CONDITIONS:
            raise ValueError(condition)
        return StubState(
            posture=posture,
            pose=Pose() if pose is None else Pose(**{k: float(pose[k]) for k in ("x", "y", "yaw")}),
            command=None if command is None else (float(command[0]), float(command[1]), float(command[2])),
            sequence_s=float(data.get("sequence_s", 0.0)),
            condition=condition,
        )
    except (TypeError, KeyError, ValueError, AttributeError, IndexError) as e:
        raise ValueError(f"invalid stub state file {path}: {data!r}") from e


def read_posture(path: Path | None = None) -> StubPosture:
    return read_state(path).posture


def _write_state(state: StubState, path: Path) -> None:
    """Atomically replace the state file (temp file in the same folder + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _check_posture(posture: str) -> None:
    if posture not in STUB_POSTURES:
        raise ValueError(f"posture must be one of {STUB_POSTURES}, got {posture!r}")


def write_posture(posture: StubPosture, path: Path | None = None) -> None:
    """Set the posture, keeping the rest."""
    _check_posture(posture)
    path = path or state_file()
    _write_state(replace(read_state(path), posture=posture), path)


def write_pose(pose: Pose, path: Path | None = None) -> None:
    """Set the pose, keeping the rest."""
    path = path or state_file()
    _write_state(replace(read_state(path), pose=pose), path)


def write_command(command: Command | None, path: Path | None = None) -> None:
    """Set or clear the running command, keeping the rest; a command set from none, or cleared, starts a new
    Move sequence, which ends a stuck condition. A fallen robot keeps no command."""
    path = path or state_file()
    state = read_state(path)
    if state.condition == "fallen":
        return
    if command is not None and state.command is not None:
        _write_state(replace(state, command=command), path)
    else:
        _write_state(replace(state, command=command, sequence_s=0.0, condition=None), path)


def reset(posture: StubPosture, path: Path | None = None) -> None:
    """Put the stub into a known state: ``posture`` at the origin, no command; the dispatcher's only write to
    stub state."""
    _check_posture(posture)
    _write_state(StubState(posture=posture), path or state_file())


# --- faults -----------------------------------------------------------------------


class _FaultInjector:
    """The skill process's fault state: only its first action call is faulted."""

    def __init__(self) -> None:
        self.consumed = False

    def apply_fault(self) -> str | None:
        """Apply the injected fault to this call. May hang forever, exit the process or write to stdout;
        otherwise returns ``None``, ``"error"`` or a service fault for the robot service to apply."""
        if self.consumed:
            return None
        self.consumed = True
        kind = os.environ.get(STUB_FAULT) or None
        if kind is not None and kind not in FAULT_KINDS:
            raise ValueError(f"{STUB_FAULT}={kind!r} is not one of {', '.join(FAULT_KINDS)}")
        if kind == "hang":
            print("stub fault: hang", file=sys.stderr, flush=True)
            while True:
                time.sleep(HANG_POLL_S)  # unscaled, forever
        if kind == "crash":
            print("stub fault: crash", file=sys.stderr, flush=True)
            os._exit(CRASH_EXIT_CODE)
        if kind == "garbage":
            print("stub fault: garbage", file=sys.stderr, flush=True)
            process.write_raw_stdout(GARBAGE_TEXT)
            os._exit(0)
        return kind  # None, "error" or a service fault


_faults = _FaultInjector()


def inject_fault() -> str | None:
    """A skill's action call for fault injection, in the skill process before it asks a service."""
    return _faults.apply_fault()


def apply_service_fault(kind: str) -> None:
    """The robot service's side of a service fault: hang (the whole process stops, sockets stay open) or crash."""
    print(f"stub fault: {kind}", file=sys.stderr, flush=True)
    if kind == "service_crash":
        os._exit(CRASH_EXIT_CODE)
    os.kill(os.getpid(), signal.SIGSTOP)


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
            _write_state(replace(state, posture="sitting", command=None, sequence_s=0.0, condition=None), self.path)
        sleep(STAND_DOWN_S)
        return 0

    def Stretch(self) -> int:
        state = read_state(self.path)
        if state.condition == "fallen":
            return 0
        if state.posture != "standing":
            return STUB_ERR_NOT_STANDING
        sleep(STRETCH_S)
        return 0

    def inject(self, condition: Condition) -> None:
        """The robot service's side of a robot fault: fallen drops the command and lasts until reset; stuck holds
        the running Move sequence in place and does nothing without one."""
        with self._lock:
            state = read_state(self.path)
            if condition == "fallen":
                _write_state(replace(state, command=None, sequence_s=0.0, condition="fallen"), self.path)
            elif state.command is not None and state.condition is None:
                _write_state(replace(state, condition="stuck"), self.path)

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
                _write_state(replace(state, pose=pose, sequence_s=state.sequence_s + waited), self.path)


class StubDetector:
    """The vision service's stub camera; faults are applied in the skill process (inject_fault)."""

    def warm_up(self) -> None:
        pass

    def detect(self, target: str) -> DetectResult:
        sleep(DETECT_S)
        obj = objects().get(target)
        return DetectResult(found=False) if obj is None else project(read_state().pose, obj)


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
