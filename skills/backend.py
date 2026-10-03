"""Robot backend selection (skills/docs/robot.md).

Dispatches on env ``GO2_BACKEND`` (``real`` | ``stub``). The backend modules are
imported lazily, and ``real`` imports its third-party dependencies only inside
functions, so importing this module has no side effects.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import ModuleType

BACKEND_ENV = "GO2_BACKEND"
BACKENDS = ("real", "stub")


class BackendNotConfigured(Exception):
    """GO2_BACKEND missing or unknown; mapped to code ``backend_not_configured``."""


class StateUnavailable(Exception):
    """No robot state could be sampled."""


class DetectorError(Exception):
    """Base for detector failures; ``code`` is the skill error code (skills/docs/skills.md)."""

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


def _forward(name: str):
    """A function that calls ``name`` on the configured backend module at call time."""

    def call(*args, **kwargs):
        return getattr(_impl(), name)(*args, **kwargs)

    call.__name__ = name
    return call


get_sport_client = _forward("get_sport_client")  # object with Move, StopMove, StandDown, Stretch -> int
get_detector = _forward("get_detector")  # object with detect(target) -> DetectResult
sample_state = _forward("sample_state")  # -> skills.schema.RobotState
sleep = _forward("sleep")  # real: time.sleep(seconds); stub: scaled by time_scale
