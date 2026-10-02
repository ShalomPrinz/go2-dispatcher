"""Real Unitree Go2 backend (skills/docs/robot.md).

All third-party imports (unitree_sdk2py, cv2, numpy, ultralytics) are inside
functions, so importing this module needs only the standard library.
"""

from __future__ import annotations

import math
import os
import threading
import time

from skills.backend import (
    BackendNotConfigured,
    BadFrame,
    CameraUnavailable,
    DetectResult,
    StateUnavailable,
    WeightsMissing,
)

IFACE_ENV = "GO2_IFACE"
YOLO_WEIGHTS_ENV = "GO2_YOLO_WEIGHTS"
DDS_DOMAIN_ID = 0
SPORT_CLIENT_TIMEOUT_S = 10.0
SPORT_STATE_TOPIC = "rt/sportmodestate"
SUBSCRIBER_QUEUE_LEN = 10
STATE_WAIT_S = 1.0
POSTURE_SITTING_MAX_M = 0.15  # (tunable, skills/docs/robot.md)
POSTURE_STANDING_MIN_M = 0.22  # (tunable, skills/docs/robot.md)

sleep = time.sleep

_dds_ready = False
_dds_lock = threading.Lock()


def _init_dds() -> None:
    """ChannelFactoryInitialize once per process."""
    global _dds_ready
    with _dds_lock:
        if _dds_ready:
            return
        iface = os.environ.get(IFACE_ENV, "")
        if not iface:
            raise BackendNotConfigured(f"{IFACE_ENV} is not set (robot.network_interface)")
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize

        ChannelFactoryInitialize(DDS_DOMAIN_ID, iface)
        _dds_ready = True


# --- sport client -------------------------------------------------------------


def get_sport_client():
    _init_dds()
    from unitree_sdk2py.go2.sport.sport_client import SportClient

    client = SportClient()
    client.SetTimeout(SPORT_CLIENT_TIMEOUT_S)
    client.Init()
    return client


# --- detector -------------------------------------------------------------------


class RealDetector:
    """VideoClient frame + YOLO (skills/docs/skills.md). YOLO is loaded on the first detect()."""

    CAMERA_TIMEOUT_S = 3.0
    IMGSZ = 640
    CONF = 0.4
    LEFT_MAX_X = 0.4  # box centre x fraction < this -> left
    RIGHT_MIN_X = 0.6  # > this -> right, else center
    NEAR_MIN_H = 0.6  # box height fraction > this -> near
    MEDIUM_MIN_H = 0.3  # > this -> medium, else far
    CONFIDENCE_DECIMALS = 2

    def __init__(self) -> None:
        self._model = None

    def _load_model(self):
        if self._model is None:
            weights = os.environ.get(YOLO_WEIGHTS_ENV, "")
            if not weights or not os.path.isfile(weights):
                # never let ultralytics download weights automatically
                raise WeightsMissing(f"YOLO weights file not found: {weights or '(unset)'}")
            from ultralytics import YOLO

            self._model = YOLO(weights)
        return self._model

    def _frame(self):
        _init_dds()
        from unitree_sdk2py.go2.video.video_client import VideoClient

        client = VideoClient()
        client.SetTimeout(self.CAMERA_TIMEOUT_S)
        client.Init()
        code, data = client.GetImageSample()
        if code != 0:
            raise CameraUnavailable(f"GetImageSample returned {code}")
        if not data:
            raise CameraUnavailable("GetImageSample returned no data")
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(bytes(data), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise BadFrame("camera frame could not be decoded")
        return image

    def detect(self, target: str) -> DetectResult:
        model = self._load_model()
        image = self._frame()
        height, width = image.shape[:2]
        best = None  # (confidence, x1, y1, x2, y2)
        for res in model.predict(image, imgsz=self.IMGSZ, conf=self.CONF, verbose=False):
            names = res.names
            for box in res.boxes:
                if names[int(box.cls[0])] != target:
                    continue
                conf = float(box.conf[0])
                if best is None or conf > best[0]:
                    best = (conf, *[float(v) for v in box.xyxy[0].tolist()])
        if best is None:
            return DetectResult(found=False)
        conf, x1, y1, x2, y2 = best
        cx = (x1 + x2) / 2.0 / width
        h = (y2 - y1) / height
        position = "left" if cx < self.LEFT_MAX_X else "right" if cx > self.RIGHT_MIN_X else "center"
        closeness = "near" if h > self.NEAR_MIN_H else "medium" if h > self.MEDIUM_MIN_H else "far"
        return DetectResult(
            found=True, position=position, closeness=closeness, confidence=round(conf, self.CONFIDENCE_DECIMALS)
        )


def get_detector() -> RealDetector:
    return RealDetector()


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
    if len(items) != n or any(x is None for x in items):
        return None
    return items


def derive_posture(body_height: float | None, mode: int | None) -> str:
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


def _state_from_msg(msg) -> dict:
    imu = getattr(msg, "imu_state", None)
    body_height = _float(getattr(msg, "body_height", None))
    mode = _int(getattr(msg, "mode", None))
    return {
        "t": time.time(),
        "backend": "real",
        "posture": derive_posture(body_height, mode),
        "mode": mode,
        "gait_type": _int(getattr(msg, "gait_type", None)),
        "body_height": body_height,
        "position": _floats(getattr(msg, "position", None), 3),
        "velocity": _floats(getattr(msg, "velocity", None), 3),
        "yaw_speed": _float(getattr(msg, "yaw_speed", None)),
        "imu_rpy": _floats(getattr(imu, "rpy", None), 3),
        "foot_force": _floats(getattr(msg, "foot_force", None), 4),
        "error_code": _int(getattr(msg, "error_code", None)),
    }


def sample_state() -> dict:
    """Wait up to STATE_WAIT_S for a message that arrived after this call; else use
    the latest earlier one; else raise StateUnavailable."""
    called = time.monotonic()
    _init_dds()
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
