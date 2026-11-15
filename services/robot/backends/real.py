"""Real Unitree Go2 backend (services/robot/docs/robot.md).

All third-party imports (unitree_sdk2py) are inside functions, so importing this module
needs only the standard library. DDS init is services/backend/dds.py, shared with the detector in
services/vision/detectors/real.py, used only by the vision service.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

from contract.schema import ObstacleDistances, Posture, RobotState
from services.backend import dds
from services.robot.backends import ArmHook, SportClient, Wait
from services.robot.protocol import SPORT_CLIENT_TIMEOUT_S, StateUnavailable
from services.robot.state_store import STATE_WAIT_S, StateStore

SPORT_STATE_TOPIC = "rt/sportmodestate"
SUBSCRIBER_QUEUE_LEN = 10
POSTURE_SITTING_MAX_M = 0.15  # (tunable, services/robot/docs/robot.md)
POSTURE_STANDING_MIN_M = 0.22  # (tunable, services/robot/docs/robot.md)
RANGE_OBSTACLE_ORDER = (
    "front",
    "back",
    "left",
    "right",
)  # assumed index order, unverified (services/robot/docs/robot.md)
RANGE_OBSTACLE_MAX_M = (
    30.0  # larger values are taken as a "nothing seen" sentinel (tunable, services/robot/docs/robot.md)
)

# --- sport client -------------------------------------------------------------


def get_sport_client() -> SportClient:
    """One SportClient: the robot service's for its lifetime, or the stop_move utility's own."""
    dds.init_dds()
    from unitree_sdk2py.go2.sport.sport_client import SportClient

    client = SportClient()
    client.SetTimeout(SPORT_CLIENT_TIMEOUT_S)
    client.Init()
    return client


# --- state ------------------------------------------------------------------------


def subscribe_state(on_message: Callable[[Any], None]) -> Any:
    """Init DDS and subscribe ``on_message`` to rt/sportmodestate; keep the returned subscriber alive."""
    dds.init_dds()
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_go.msg.dds_ import SportModeState_

    sub = ChannelSubscriber(SPORT_STATE_TOPIC, SportModeState_)
    sub.Init(on_message, SUBSCRIBER_QUEUE_LEN)
    return sub


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
    """The single posture rule (services/robot/docs/robot.md): body_height < SITTING_MAX -> "sitting";
    >= STANDING_MIN -> "standing"; otherwise or None -> "unknown". `mode` is unused in v1."""
    del mode  # unused in v1 (services/robot/docs/robot.md)
    if body_height is None:
        return "unknown"
    if body_height < POSTURE_SITTING_MAX_M:
        return "sitting"
    if body_height >= POSTURE_STANDING_MIN_M:
        return "standing"
    return "unknown"


def state_from_msg(msg) -> RobotState:
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


# --- the backend -------------------------------------------------------------------


def _wait(seconds: float, abort: threading.Event) -> None:
    abort.wait(seconds)


class _NoArmHook:
    """Nothing happens at an arm on the robot."""

    def clear(self) -> None:
        pass

    def arm(self) -> None:
        pass


class RealBackend:
    """The real Go2 (services/robot/docs/robot.md#real-backend): DDS, SportClient and rt/sportmodestate."""

    name = "real"

    def __init__(self) -> None:
        self._store = StateStore(state_from_msg)  # this process's own subscription: the utilities only
        self._subscriber: Any = None

    def init_dds(self) -> None:
        dds.init_dds()

    def setup_timing(self) -> dict[str, float]:
        ms = dds.dds_init_ms()
        return {} if ms is None else {"dds_init_ms": ms}

    def sport_client(self) -> SportClient:
        return get_sport_client()

    def sample_state(self) -> RobotState:
        """The utilities' own DDS read: wait up to STATE_WAIT_S for a message that arrived after this call; else
        use the latest earlier one; else raise StateUnavailable. Skills read state through the robot service."""
        min_seq = self._store.seq + 1
        if self._subscriber is None:
            self._subscriber = subscribe_state(self._store.put)
        reading = self._store.read(min_seq, STATE_WAIT_S)
        if reading is None:
            raise StateUnavailable(f"no {SPORT_STATE_TOPIC} message within {STATE_WAIT_S:g}s")
        return reading.state

    def start_source(self) -> tuple[StateStore, object]:
        """The service's store and its rt/sportmodestate subscriber (DDS init happens here)."""
        store = StateStore(state_from_msg)
        return store, subscribe_state(store.put)

    def motion(self, store: StateStore) -> tuple[SportClient, Wait, ArmHook]:
        """The service's SportClient, after the state subscription's DDS init; the motion clock is real time and
        an arm does nothing here."""
        return get_sport_client(), _wait, _NoArmHook()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
