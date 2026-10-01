"""Robot backend selection (docs/robot.md).

Dispatches on env ``GO2_BACKEND`` (``real`` | ``stub``). The backend modules are
imported lazily, and ``real`` imports its third-party dependencies only inside
functions, so importing this module has no side effects.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from types import ModuleType

BACKEND_ENV = "GO2_BACKEND"
BACKENDS = ("real", "stub")


class BackendNotConfigured(Exception):
    """GO2_BACKEND missing or unknown; mapped to code ``backend_not_configured``."""


class StateUnavailable(Exception):
    """No robot state could be sampled."""


class DetectorError(Exception):
    """Base for detector failures; ``code`` is the skill error code (docs/skills.md)."""

    code = "detector_error"


class CameraUnavailable(DetectorError):
    code = "camera_unavailable"


class BadFrame(DetectorError):
    code = "bad_frame"


class WeightsMissing(DetectorError):
    code = "weights_missing"


@dataclass
class DetectResult:
    found: bool
    position: str | None = None
    closeness: str | None = None
    confidence: float | None = None


def backend_name() -> str:
    """The configured backend name; raises BackendNotConfigured."""
    name = os.environ.get(BACKEND_ENV, "")
    if name not in BACKENDS:
        if not name:
            raise BackendNotConfigured(f"{BACKEND_ENV} is not set (expected real or stub)")
        raise BackendNotConfigured(f"{BACKEND_ENV}={name!r} is not one of real, stub")
    return name


def _impl() -> ModuleType:
    if backend_name() == "real":
        from skills import real
        return real
    from skills import stub
    return stub


def get_sport_client():
    """Object with Move, StopMove, StandDown, Stretch -> int."""
    return _impl().get_sport_client()


def get_detector():
    """Object with detect(target) -> DetectResult."""
    return _impl().get_detector()


def sample_state() -> dict:
    """RobotState as a plain dict."""
    return _impl().sample_state()


def sleep(seconds: float) -> None:
    """Real: time.sleep(seconds); stub: time.sleep(seconds * time_scale)."""
    if backend_name() == "stub":
        from skills import stub
        stub.sleep(seconds)
    else:
        time.sleep(seconds)
