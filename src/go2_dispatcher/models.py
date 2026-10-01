"""Data models and exceptions (§6)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


# --- Exceptions (§6.6) -------------------------------------------------------


class ConfigError(Exception):
    """Invalid configuration (unknown key, invalid value, missing explicit file)."""


class RegistryError(Exception):
    """Invalid skill set; the message names the offending file."""


class BusyError(Exception):
    """run_task called while a task is running."""


class LLMUnavailable(Exception):
    """Infra retries exhausted, or a non-retryable API error."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMInterrupted(Exception):
    """Stop or task deadline hit while waiting to retry an LLM call."""

    def __init__(self, cause: Literal["operator", "task_time_limit"]):
        super().__init__(cause)
        self.cause = cause


# --- SkillResponse (§6.2) -----------------------------------------------------


class SkillError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str                               # one line, written for the LLM


class RobotState(BaseModel):
    model_config = ConfigDict(extra="allow")   # tolerate extra fields from the robot

    t: float                                   # unix time when sampled (local clock)
    backend: Literal["real", "stub"]
    posture: Literal["standing", "sitting", "unknown"]
    mode: int | None = None
    gait_type: int | None = None
    body_height: float | None = None
    position: list[float] | None = None        # [x, y, z], robot's own estimate if published
    velocity: list[float] | None = None        # [vx, vy, vz]
    yaw_speed: float | None = None
    imu_rpy: list[float] | None = None         # [roll, pitch, yaw] radians
    foot_force: list[float] | None = None      # 4 values
    error_code: int | None = None


class SkillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    skill: str
    status: Literal["ok", "error"]
    observations: dict[str, Any] = {}
    error: SkillError | None = None            # present iff status == "error"
    state_before: RobotState | None = None
    state_after: RobotState | None = None
    state_error: str | None = None             # messages joined with "; "
    timing: dict[str, float] = {}              # init_ms, exec_ms, state_ms, total_ms (+ stop_call_ms)

    @model_validator(mode="after")
    def _error_iff_status_error(self) -> "SkillResponse":
        if (self.status == "error") != (self.error is not None):
            raise ValueError("error must be present if and only if status == 'error'")
        return self
