"""Stub backend (skills/docs/skills.md): remembers posture in a shared JSON state file, supports fault
injection and configured detections. Replaces only the SDK layer inside the skill subprocess."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal, get_args

from skills import process
from skills.backend import CameraUnavailable, DetectResult
from skills.env import STUB_DETECTIONS, STUB_FAULT, STUB_STATE_FILE, STUB_TIME_SCALE
from skills.schema import RobotState

# Stub defaults: used when the env is unset (manual runs) and as the dispatcher's stub config defaults.
DEFAULT_STATE_FILE = "runs/.stub_state.json"
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
DETECT_CONFIDENCE = 0.9
BODY_HEIGHT_STANDING_M = 0.32
BODY_HEIGHT_SITTING_M = 0.08

_fault_consumed = False


class StubCameraError(CameraUnavailable):
    """Injected detector fault; the skill maps it to ``camera_unavailable``."""


# --- settings -----------------------------------------------------------------


def state_file() -> Path:
    return Path(os.environ.get(STUB_STATE_FILE) or DEFAULT_STATE_FILE)


def time_scale() -> float:
    raw = os.environ.get(STUB_TIME_SCALE)
    return float(raw) if raw else DEFAULT_TIME_SCALE


def detections() -> dict[str, str]:
    raw = os.environ.get(STUB_DETECTIONS)
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{STUB_DETECTIONS} must be a JSON object")
    return data


def sleep(seconds: float) -> None:
    time.sleep(seconds * time_scale())


# --- state file -----------------------------------------------------------------


def read_posture(path: Path | None = None) -> StubPosture:
    """Posture from the state file; a missing file means standing."""
    path = path or state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_POSTURE
    posture = data.get("posture") if isinstance(data, dict) else None
    if posture not in STUB_POSTURES:
        raise ValueError(f"invalid stub state file {path}: {data!r}")
    return posture


def write_posture(posture: StubPosture, path: Path | None = None) -> None:
    """Atomically write ``{"posture": ...}`` (temp file in the same folder + os.replace)."""
    if posture not in STUB_POSTURES:
        raise ValueError(f"posture must be one of {STUB_POSTURES}, got {posture!r}")
    path = path or state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"posture": posture}, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- faults -----------------------------------------------------------------------


def disable_faults() -> None:
    """No injected fault in this process; the utilities call it so the stop path always works."""
    global _fault_consumed
    _fault_consumed = True


def _take_fault() -> str | None:
    """The injected fault kind for this call: only the first action call is faulted."""
    global _fault_consumed
    if _fault_consumed:
        return None
    _fault_consumed = True
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


# --- clients ----------------------------------------------------------------------


class StubSportClient:
    """Every method returns int, 0 = success."""

    def Move(self, vx: float, vy: float, vyaw: float) -> int:
        if _take_fault() == "error":
            return STUB_ERR_INJECTED
        return 0 if read_posture() == "standing" else STUB_ERR_NOT_STANDING

    def StopMove(self) -> int:
        if _take_fault() == "error":
            return STUB_ERR_INJECTED
        return 0

    def StandDown(self) -> int:
        if _take_fault() == "error":
            return STUB_ERR_INJECTED
        if read_posture() == "standing":
            write_posture("sitting")
            sleep(STAND_DOWN_S)
        return 0

    def Stretch(self) -> int:
        if _take_fault() == "error":
            return STUB_ERR_INJECTED
        if read_posture() != "standing":
            return STUB_ERR_NOT_STANDING
        sleep(STRETCH_S)
        return 0


class StubDetector:
    def detect(self, target: str) -> DetectResult:
        if _take_fault() == "error":
            raise StubCameraError("injected camera fault")
        sleep(DETECT_S)
        found = detections().get(target)
        if found is None:
            return DetectResult(found=False)
        position, closeness = found.split(":", 1)
        return DetectResult(found=True, position=position, closeness=closeness, confidence=DETECT_CONFIDENCE)


def get_sport_client() -> StubSportClient:
    return StubSportClient()


def get_detector() -> StubDetector:
    return StubDetector()


def sample_state() -> RobotState:
    posture = read_posture()
    return RobotState(
        t=time.time(),
        backend="stub",
        posture=posture,
        body_height=BODY_HEIGHT_STANDING_M if posture == "standing" else BODY_HEIGHT_SITTING_M,
    )
