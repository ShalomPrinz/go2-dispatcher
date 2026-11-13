"""The executor's step outcome when the robot service is lost (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

from contract.schema import ErrorCode
from dispatcher import prompts
from dispatcher.executor import _parse_response, _step_outcome
from dispatcher.models import StopCause
from skills.runner import SkillOutcome, StateSampler, build_response, to_json


def _reply(outcome: SkillOutcome):
    return _parse_response(to_json(build_response("walk", outcome, StateSampler(), 0.0)), "walk")


UNAVAILABLE = _reply(
    SkillOutcome.error(ErrorCode.ROBOT_UNAVAILABLE, "ServiceUnavailable: connection lost", observations={}, timing={})
)
LOST = ("error", None, "robot_service_lost", prompts.KILL_ROBOT_SERVICE_LOST)


def test_a_service_loss_overrides_the_skills_own_reply():
    """The skill replied before the loss was detected (or before its kill landed): the loss still decides."""
    assert _step_outcome(None, True, UNAVAILABLE, 1, 10.0) == LOST
    assert _step_outcome(None, True, _reply(SkillOutcome.ok(observations={}, timing={})), 0, 10.0) == LOST
    assert _step_outcome(StopCause.ROBOT_SERVICE_LOST, True, None, -9, 10.0) == LOST


def test_without_a_loss_the_skills_reply_and_other_kill_causes_decide():
    assert _step_outcome(None, False, UNAVAILABLE, 1, 10.0)[2] == "robot_unavailable"
    operator = _step_outcome(StopCause.OPERATOR, True, None, -9, 10.0)  # a loss during a stop keeps the stop
    assert operator[:3] == ("interrupted", StopCause.OPERATOR, "stopped_by_operator")
