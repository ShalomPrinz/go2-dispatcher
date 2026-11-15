"""A started step's outcome from its kill cause, the stop and the skill's reply
(dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

from dataclasses import dataclass

from .. import prompts
from ..models import ExecOutcome, SkillResponse, StopCause


@dataclass(frozen=True)
class StepVerdict:
    outcome: ExecOutcome
    interrupt_cause: StopCause | None  # set iff outcome is interrupted
    error_code: str | None
    error_message: str | None


SERVICE_LOST = StepVerdict("error", None, StopCause.ROBOT_SERVICE_LOST.value, prompts.KILL_ROBOT_SERVICE_LOST)


def step_outcome(
    cause: StopCause | None, service_lost: bool, response: SkillResponse | None, rc: int | None, timeout_s: float
) -> StepVerdict:
    """A service loss with no other kill cause overrides the skill's own reply."""
    if cause == StopCause.ROBOT_SERVICE_LOST or (cause is None and service_lost):
        return SERVICE_LOST
    if cause is not None:
        outcome, error_code, template = cause.kill
        return StepVerdict(
            outcome, cause if outcome == "interrupted" else None, error_code, template.format(timeout_s=timeout_s)
        )
    if response is None:
        return StepVerdict("malformed", None, "malformed", prompts.MALFORMED_RESPONSE.format(rc=rc))
    error = response.error
    return StepVerdict(response.status, None, error.code if error else None, error.message if error else None)
