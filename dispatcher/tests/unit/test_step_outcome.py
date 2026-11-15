"""The executor's step outcome when the robot service is lost (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

from contract.runtime import SkillOutcome, StateSampler, build_response, to_json
from contract.schema import ErrorCode, RobotState
from dispatcher import prompts
from dispatcher.execution.outcome import StepVerdict, step_outcome
from dispatcher.execution.process import parse_response
from dispatcher.models import StopCause


def _no_state() -> RobotState:
    raise AssertionError("the sampler is never taken here")


def _reply(outcome: SkillOutcome):
    return parse_response(to_json(build_response("walk", outcome, StateSampler(_no_state), 0.0)), "walk")


UNAVAILABLE = _reply(
    SkillOutcome.error(ErrorCode.ROBOT_UNAVAILABLE, "ServiceUnavailable: connection lost", observations={}, timing={})
)
LOST = StepVerdict("error", None, "robot_service_lost", prompts.KILL_ROBOT_SERVICE_LOST)


def test_a_service_loss_overrides_the_skills_own_reply():
    """The skill replied before the loss was detected (or before its kill landed): the loss still decides."""
    assert step_outcome(None, True, UNAVAILABLE, 1, 10.0) == LOST
    assert step_outcome(None, True, _reply(SkillOutcome.ok(observations={}, timing={})), 0, 10.0) == LOST
    assert step_outcome(StopCause.ROBOT_SERVICE_LOST, True, None, -9, 10.0) == LOST


def test_without_a_loss_the_skills_reply_and_other_kill_causes_decide():
    assert step_outcome(None, False, UNAVAILABLE, 1, 10.0).error_code == "robot_unavailable"
    operator = step_outcome(StopCause.OPERATOR, True, None, -9, 10.0)  # a loss during a stop keeps the stop
    assert (operator.outcome, operator.interrupt_cause, operator.error_code) == (
        "interrupted",
        StopCause.OPERATOR,
        "stopped_by_operator",
    )
