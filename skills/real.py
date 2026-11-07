"""Real Unitree Go2 backend (skills/docs/robot.md).

All third-party imports (unitree_sdk2py) are inside functions, so importing this module
needs only the standard library. The detector is in skills/real_detector.py, used only by
the vision service.
"""

from __future__ import annotations

import math
import os
import threading
import time

from skills.backend import BackendNotConfigured, StateUnavailable
from skills.env import IFACE
from skills.schema import ObstacleDistances, Posture, RobotState

DDS_DOMAIN_ID = 0
SPORT_CLIENT_TIMEOUT_S = 5.0  # per-call reply wait; must fit in the process timeouts (tunable, skills/docs/skills.md)
SPORT_STATE_TOPIC = "rt/sportmodestate"
SUBSCRIBER_QUEUE_LEN = 10
STATE_WAIT_S = 1.0
POSTURE_SITTING_MAX_M = 0.15  # (tunable, skills/docs/robot.md)
POSTURE_STANDING_MIN_M = 0.22  # (tunable, skills/docs/robot.md)
RANGE_OBSTACLE_ORDER = ("front", "back", "left", "right")  # assumed index order, unverified (skills/docs/robot.md)
RANGE_OBSTACLE_MAX_M = 30.0  # larger values are taken as a "nothing seen" sentinel (tunable, skills/docs/robot.md)

sleep = time.sleep

_dds_ready = False
_dds_lock = threading.Lock()


def init_dds() -> None:
    """ChannelFactoryInitialize once per process."""
    global _dds_ready
    with _dds_lock:
        if _dds_ready:
            return
        iface = os.environ.get(IFACE, "")
        if not iface:
            raise BackendNotConfigured(f"{IFACE} is not set (robot.network_interface)")
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize

        ChannelFactoryInitialize(DDS_DOMAIN_ID, iface)
        _dds_ready = True


# --- sport client -------------------------------------------------------------


def get_sport_client():
    init_dds()
    from unitree_sdk2py.go2.sport.sport_client import SportClient

    client = SportClient()
    client.SetTimeout(SPORT_CLIENT_TIMEOUT_S)
    client.Init()
    return client


# --- state ------------------------------------------------------------------------

_state_cond = threading.Condition()
_latest: tuple[float, object] | None = None  # (local arrival monotonic, msg)
_subscriber = None


def _on_state(msg) -> None:
    global _latest
    with _state_cond:
        _latest = (time.monotonic(), msg)
        _state_cond.notify_all()


def _ensure_subscriber() -> None:
    global _subscriber
    if _subscriber is not None:
        return
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_

    sub = ChannelSubscriber(SPORT_STATE_TOPIC, SportModeState_)
    sub.Init(_on_state, SUBSCRIBER_QUEUE_LEN)
    _subscriber = sub


def _plain(v):
    """numpy / ctypes scalar -> plain Python number."""
    if hasattr(v, "item"):
        v = v.item()
    if hasattr(v, "value") and not isinstance(v, (int, float)):
        v = v.value
    return v


def _float(v) -> float | None:
    try:
        f = float(_plain(v))
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(v) -> int | None:
    v = _plain(v)
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _floats(v, n: int) -> list[float] | None:
    """A list of n finite floats, else None (one bad element nulls the whole list)."""
    if v is None:
        return None
    try:
        items = [_float(x) for x in list(v)[:n]]
    except TypeError:
        return None
    floats = [x for x in items if x is not None]
    return floats if len(floats) == n else None


def _raw_floats(v, n: int) -> list[float | None] | None:
    """Exactly n values kept element by element (a non-finite or bad element becomes None), else None."""
    if v is None:
        return None
    try:
        items = list(v)
    except TypeError:
        return None
    return [_float(x) for x in items] if len(items) == n else None


def derive_obstacles(raw: list[float | None] | None) -> ObstacleDistances | None:
    """`range_obstacle` -> per-direction metres; 0, negative, above RANGE_OBSTACLE_MAX_M or None -> None."""
    if raw is None:
        return None
    known = [d if d is not None and 0 < d <= RANGE_OBSTACLE_MAX_M else None for d in raw]
    return ObstacleDistances(**dict(zip(RANGE_OBSTACLE_ORDER, known, strict=True)))


def derive_posture(body_height: float | None, mode: int | None) -> Posture:
    """The single posture rule (skills/docs/robot.md): body_height < SITTING_MAX -> "sitting";
    >= STANDING_MIN -> "standing"; otherwise or None -> "unknown". `mode` is unused in v1."""
    del mode  # unused in v1 (skills/docs/robot.md)
    if body_height is None:
        return "unknown"
    if body_height < POSTURE_SITTING_MAX_M:
        return "sitting"
    if body_height >= POSTURE_STANDING_MIN_M:
        return "standing"
    return "unknown"


def _state_from_msg(msg) -> RobotState:
    imu = getattr(msg, "imu_state", None)
    body_height = _float(getattr(msg, "body_height", None))
    mode = _int(getattr(msg, "mode", None))
    range_obstacle = _raw_floats(getattr(msg, "range_obstacle", None), len(RANGE_OBSTACLE_ORDER))
    return RobotState(
        t=time.time(),
        backend="real",
        posture=derive_posture(body_height, mode),
        mode=mode,
        gait_type=_int(getattr(msg, "gait_type", None)),
        body_height=body_height,
        position=_floats(getattr(msg, "position", None), 3),
        velocity=_floats(getattr(msg, "velocity", None), 3),
        yaw_speed=_float(getattr(msg, "yaw_speed", None)),
        imu_rpy=_floats(getattr(imu, "rpy", None), 3),
        foot_force=_floats(getattr(msg, "foot_force", None), 4),
        error_code=_int(getattr(msg, "error_code", None)),
        obstacle_m=derive_obstacles(range_obstacle),
        range_obstacle_raw=range_obstacle,
    )


def sample_state() -> RobotState:
    """Wait up to STATE_WAIT_S for a message that arrived after this call; else use
    the latest earlier one; else raise StateUnavailable."""
    called = time.monotonic()
    init_dds()
    _ensure_subscriber()
    deadline = called + STATE_WAIT_S
    with _state_cond:
        while _latest is None or _latest[0] <= called:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _state_cond.wait(remaining)
        latest = _latest
    if latest is None:
        raise StateUnavailable(f"no {SPORT_STATE_TOPIC} message within {STATE_WAIT_S:g}s")
    return _state_from_msg(latest[1])
