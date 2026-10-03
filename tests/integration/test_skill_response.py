"""Skill output validates against the shared SkillResponse as the dispatcher parses it
(skills/docs/skills.md)."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from skills.result import build_response, to_json
from skills.schema import RobotState, SkillResponse

ADAPTER = TypeAdapter(SkillResponse)
STATE = RobotState(t=1.0, backend="stub", posture="standing", body_height=0.32)


def test_ok_response_round_trips():
    sent = build_response(
        "walk",
        "ok",
        observations={"sdk_ret": 0, "distance_m": 0.9},
        state_before=STATE,
        state_after=STATE,
        timing={"init_ms": 1, "exec_ms": 2.5, "state_ms": 3, "total_ms": 7},
    )
    assert ADAPTER.validate_json(to_json(sent)) == sent


def test_error_response_round_trips():
    sent = build_response("walk", "error", error_code="sdk_error", error_message="Move returned 1", state_error="x")
    assert ADAPTER.validate_json(to_json(sent)) == sent


def test_rejects_error_mismatch_and_nested_extra_fields():
    ok = json.loads(to_json(build_response("read_state", "ok", state_after=STATE)))
    for bad in (
        {**ok, "error": {"code": "x", "message": "y"}},
        {**ok, "state_after": {**ok["state_after"], "extra_field": 5}},
        {**ok, "extra_field": 5},
    ):
        with pytest.raises(ValidationError):
            ADAPTER.validate_json(json.dumps(bad))


def test_nan_is_not_emitted():
    with pytest.raises(ValueError):
        to_json(build_response("walk", "ok", observations={"x": float("nan")}))
