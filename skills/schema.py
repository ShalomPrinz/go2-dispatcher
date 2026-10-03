"""The skill response and robot state schema, defined once for skills and dispatcher
(skills/docs/skills.md). Standard library only: skill processes never import pydantic."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, get_args

SCHEMA_VERSION = 1

Posture = Literal["standing", "sitting", "unknown"]
POSTURES: tuple[Posture, ...] = get_args(Posture)
Status = Literal["ok", "error"]


class ErrorCode(str, Enum):
    """Skill error codes (skills/docs/skills.md); members are str, so JSON gets the plain value."""

    INVALID_PARAMS = "invalid_params"
    SDK_ERROR = "sdk_error"
    BACKEND_NOT_CONFIGURED = "backend_not_configured"
    EXCEPTION = "exception"
    UNSUPPORTED_OBJECT = "unsupported_object"
    DETECTOR_ERROR = "detector_error"
    CAMERA_UNAVAILABLE = "camera_unavailable"
    BAD_FRAME = "bad_frame"
    WEIGHTS_MISSING = "weights_missing"
    STATE_UNAVAILABLE = "state_unavailable"

    def __str__(self) -> str:  # Python 3.10 has no StrEnum; str() and f-strings give the value
        return self.value


# read by pydantic when the dispatcher validates; a plain dict so no pydantic import is needed
_FORBID_EXTRA = {"extra": "forbid"}


@dataclass(frozen=True)
class SkillError:
    __pydantic_config__ = _FORBID_EXTRA

    code: str
    message: str  # one line, written for the LLM


@dataclass(frozen=True)
class RobotState:
    __pydantic_config__ = _FORBID_EXTRA

    t: float  # unix time when sampled (local clock)
    backend: Literal["real", "stub"]
    posture: Posture
    mode: int | None = None
    gait_type: int | None = None
    body_height: float | None = None
    position: list[float] | None = None  # [x, y, z], robot's own estimate if published
    velocity: list[float] | None = None  # [vx, vy, vz]
    yaw_speed: float | None = None
    imu_rpy: list[float] | None = None  # [roll, pitch, yaw] radians
    foot_force: list[float] | None = None  # 4 values
    error_code: int | None = None


@dataclass(frozen=True)
class SkillResponse:
    __pydantic_config__ = _FORBID_EXTRA

    schema_version: Literal[1]
    skill: str
    status: Status
    observations: dict[str, Any] = field(default_factory=dict)
    error: SkillError | None = None  # present iff status == "error"
    state_before: RobotState | None = None
    state_after: RobotState | None = None
    state_error: str | None = None  # messages joined with "; "
    timing: dict[str, float] = field(default_factory=dict)  # init_ms, exec_ms, state_ms, total_ms (+ stop_call_ms)

    def __post_init__(self):  # under pydantic, a ValueError here becomes a ValidationError
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error must be present if and only if status == 'error'")
