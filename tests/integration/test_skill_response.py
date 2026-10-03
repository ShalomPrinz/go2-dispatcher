"""Skill output validates against the shared SkillResponse as the dispatcher parses it
(skills/docs/skills.md)."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from skills.runner import SkillOutcome, StateSampler, build_response, to_json
from skills.schema import ErrorCode, RobotState, SkillResponse

ADAPTER = TypeAdapter(SkillResponse)
STATE = RobotState(t=1.0, backend="stub", posture="standing", body_height=0.32)


def test_ok_response_round_trips():
    outcome = SkillOutcome.ok(observations={"sdk_ret": 0, "distance_m": 0.9}, timing={"init_ms": 1, "exec_ms": 2.5})
    sent = build_response("walk", outcome, StateSampler(before=STATE, after=STATE, seconds=0.003, used=True), 7)
    assert list(sent.timing) == ["init_ms", "exec_ms", "state_ms", "total_ms"]
    assert ADAPTER.validate_json(to_json(sent)) == sent


def test_error_response_round_trips():
    outcome = SkillOutcome.error(ErrorCode.SDK_ERROR, "Move returned 1", observations={}, timing={})
    sent = build_response("walk", outcome, StateSampler(errors=["before: x"]), 7)
    assert ADAPTER.validate_json(to_json(sent)) == sent


def test_rejects_error_mismatch_and_nested_extra_fields():
    response = build_response("read_state", SkillOutcome.ok(observations={}, timing={}), StateSampler(after=STATE), 1)
    ok = json.loads(to_json(response))
    for bad in (
        {**ok, "error": {"code": "x", "message": "y"}},
        {**ok, "state_after": {**ok["state_after"], "extra_field": 5}},
        {**ok, "extra_field": 5},
    ):
        with pytest.raises(ValidationError):
            ADAPTER.validate_json(json.dumps(bad))


def test_nan_is_not_emitted():
    with pytest.raises(ValueError):
        to_json(build_response("walk", SkillOutcome.ok(observations={"x": float("nan")}, timing={}), StateSampler(), 1))
