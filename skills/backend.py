"""Robot backend selection and the errors shared by the backends (skills/docs/robot.md).

Reads env ``GO2_BACKEND`` (``real`` | ``stub``). Never imports ``skills.real``: skill processes do not touch
DDS. The utilities' own robot access is in skills/local.py, the robot service's in skills/robot_service.py.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from contract.env import BACKEND
from contract.schema import ErrorCode

BACKENDS = ("real", "stub")


class BackendNotConfigured(Exception):
    """GO2_BACKEND missing or unknown; mapped to code ``backend_not_configured``."""


class StateUnavailable(Exception):
    """No robot state could be sampled."""


class DetectorError(Exception):
    """Base for detector failures; ``code`` is the skill error code (contract/docs/contract.md)."""

    code = ErrorCode.DETECTOR_ERROR


class CameraUnavailable(DetectorError):
    code = ErrorCode.CAMERA_UNAVAILABLE


class BadFrame(DetectorError):
    code = ErrorCode.BAD_FRAME


class WeightsMissing(DetectorError):
    code = ErrorCode.WEIGHTS_MISSING


class VisionUnavailable(DetectorError):
    """The vision service could not be reached or did not answer (skills/docs/skills.md)."""

    code = ErrorCode.VISION_UNAVAILABLE


@dataclass
class DetectResult:
    found: bool
    position: str | None = None
    closeness: str | None = None
    confidence: float | None = None


def backend_name() -> str:
    """The configured backend name; raises BackendNotConfigured."""
    name = os.environ.get(BACKEND, "")
    if name not in BACKENDS:
        if not name:
            raise BackendNotConfigured(f"{BACKEND} is not set (expected real or stub)")
        raise BackendNotConfigured(f"{BACKEND}={name!r} is not one of real, stub")
    return name


def sleep(seconds: float) -> None:
    """A skill's wait: real time on the robot, scaled by the stub time scale on the stub."""
    if backend_name() == "stub":
        from skills import stub

        stub.sleep(seconds)
    else:
        time.sleep(seconds)
