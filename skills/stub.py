"""Stub backend (skills/docs/skills.md): keeps posture and an odometry pose in a shared JSON state file,
simulates the front camera over objects placed in the world, and supports fault injection. Replaces only
the SDK layer inside the skill subprocess."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, get_args

from skills import process, vision
from skills.backend import CameraUnavailable, DetectResult
from skills.env import STUB_FAULT, STUB_OBJECTS, STUB_STATE_FILE, STUB_TIME_SCALE
from skills.schema import RobotState

# Stub defaults: used when the env is unset (manual runs) and as the dispatcher's stub config defaults.
DEFAULT_STATE_FILE = "logs/.stub_state.json"
DEFAULT_TIME_SCALE = 0.1

StubPosture = Literal["standing", "sitting"]  # the postures the stub can hold; a subset of schema.Posture
STUB_POSTURES: tuple[StubPosture, ...] = get_args(StubPosture)
DEFAULT_POSTURE: StubPosture = "standing"

STUB_ERR_NOT_STANDING = 1
STUB_ERR_INJECTED = 99
FaultKind = Literal["error", "hang", "crash", "garbage"]
FAULT_KINDS: tuple[FaultKind, ...] = get_args(FaultKind)
CRASH_EXIT_CODE = 139
GARBAGE_TEXT = "not json\n"
HANG_POLL_S = 1.0

STAND_DOWN_S = 1.5
STRETCH_S = 3.0
DETECT_S = 0.5
BODY_HEIGHT_STANDING_M = 0.32
BODY_HEIGHT_SITTING_M = 0.08

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

_command: tuple[float, float, float] | None = None  # last accepted Move(vx, vy, vyaw), until StopMove


@dataclass(frozen=True)
class Pose:
    """Odometry pose in the world frame: metres, yaw in radians counter-clockwise from +x."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0


@dataclass(frozen=True)
class StubState:
    posture: StubPosture = DEFAULT_POSTURE
    pose: Pose = Pose()


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
    """Wait ``seconds`` scaled by the time scale; a running Move advances the pose by the unscaled seconds."""
    time.sleep(seconds * time_scale())
    if _command is not None:
        write_pose(advance(read_state().pose, _command, seconds))


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


# --- state file -----------------------------------------------------------------


def read_state(path: Path | None = None) -> StubState:
    """The state file; a missing file means standing at the origin, a missing pose means the origin."""
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
        return StubState(
            posture=posture, pose=Pose() if pose is None else Pose(**{k: float(pose[k]) for k in ("x", "y", "yaw")})
        )
    except (TypeError, KeyError, ValueError, AttributeError) as e:
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
    """Set the posture, keeping the pose."""
    _check_posture(posture)
    path = path or state_file()
    _write_state(StubState(posture=posture, pose=read_state(path).pose), path)


def write_pose(pose: Pose, path: Path | None = None) -> None:
    """Set the pose, keeping the posture."""
    path = path or state_file()
    _write_state(StubState(posture=read_state(path).posture, pose=pose), path)


def reset(posture: StubPosture, path: Path | None = None) -> None:
    """Put the stub into a known state: ``posture`` at the origin; the dispatcher's only write to stub state."""
    _check_posture(posture)
    _write_state(StubState(posture=posture), path or state_file())


# --- faults -----------------------------------------------------------------------


class _FaultInjector:
    """Process-wide fault state shared by every stub client: only the first action call is faulted."""

    def __init__(self) -> None:
        self.consumed = False

    def apply_fault(self) -> str | None:
        """Apply the injected fault to this call. May hang forever, exit the process or write to stdout;
        otherwise returns ``None`` or ``"error"``."""
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
        return kind  # None or "error"


_faults = _FaultInjector()


def disable_faults() -> None:
    """No injected fault in this process; the utilities call it so the stop path always works."""
    _faults.consumed = True


# --- clients ----------------------------------------------------------------------


class StubSportClient:
    """Every method returns int, 0 = success."""

    def Move(self, vx: float, vy: float, vyaw: float) -> int:
        global _command
        if _faults.apply_fault() == "error":
            return STUB_ERR_INJECTED
        if read_posture() != "standing":
            return STUB_ERR_NOT_STANDING
        _command = (vx, vy, vyaw)
        return 0

    def StopMove(self) -> int:
        global _command
        if _faults.apply_fault() == "error":
            return STUB_ERR_INJECTED
        _command = None
        return 0

    def StandDown(self) -> int:
        if _faults.apply_fault() == "error":
            return STUB_ERR_INJECTED
        if read_posture() == "standing":
            write_posture("sitting")
            sleep(STAND_DOWN_S)
        return 0

    def Stretch(self) -> int:
        if _faults.apply_fault() == "error":
            return STUB_ERR_INJECTED
        if read_posture() != "standing":
            return STUB_ERR_NOT_STANDING
        sleep(STRETCH_S)
        return 0


class StubDetector:
    def detect(self, target: str) -> DetectResult:
        if _faults.apply_fault() == "error":
            raise CameraUnavailable("injected camera fault")
        sleep(DETECT_S)
        obj = objects().get(target)
        return DetectResult(found=False) if obj is None else project(read_state().pose, obj)


def get_sport_client() -> StubSportClient:
    return StubSportClient()


def get_detector() -> StubDetector:
    return StubDetector()


def sample_state() -> RobotState:
    """Posture and pose in the real fields; velocity is the running command in the world frame (zero at rest)."""
    state = read_state()
    pose = state.pose
    height = BODY_HEIGHT_STANDING_M if state.posture == "standing" else BODY_HEIGHT_SITTING_M
    vx, vy, vyaw = _command or (0.0, 0.0, 0.0)
    cos, sin = math.cos(pose.yaw), math.sin(pose.yaw)
    return RobotState(
        t=time.time(),
        backend="stub",
        posture=state.posture,
        body_height=height,
        position=[pose.x, pose.y, height],
        velocity=[vx * cos - vy * sin, vx * sin + vy * cos, 0.0],
        yaw_speed=vyaw,
        imu_rpy=[0.0, 0.0, pose.yaw],
    )
