"""build_response dicts validate against the dispatcher's SkillResponse (skills/docs/skills.md)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dispatcher.models import SkillResponse
from skills.result import build_response

STATE = {"t": 1.0, "backend": "stub", "posture": "standing", "body_height": 0.32, "mode": None, "position": None}


def test_ok_response_validates():
    d = build_response(
        "walk",
        "ok",
        observations={"sdk_ret": 0, "distance_m": 0.9},
        state_before=STATE,
        state_after=STATE,
        timing={"init_ms": 1, "exec_ms": 2.5, "state_ms": 3, "total_ms": 7},
    )
    r = SkillResponse.model_validate(d)
    assert d["schema_version"] == 1
    assert r.status == "ok" and r.error is None
    assert r.observations == {"sdk_ret": 0, "distance_m": 0.9}
    assert r.state_after.posture == "standing"
    assert r.timing["exec_ms"] == 2.5


def test_minimal_ok_validates():
    r = SkillResponse.model_validate(build_response("sit", "ok"))
    assert r.observations == {} and r.timing == {} and r.state_before is None


def test_error_response_validates():
    d = build_response(
        "walk",
        "error",
        error_code="sdk_error",
        error_message="Move returned 1",
        state_error="before: StateUnavailable: x",
    )
    r = SkillResponse.model_validate(d)
    assert r.error.code == "sdk_error" and r.error.message == "Move returned 1"
    assert r.state_error == "before: StateUnavailable: x"


def test_model_rejects_error_mismatch():
    d = build_response("walk", "ok")
    d["error"] = {"code": "x", "message": "y"}
    with pytest.raises(ValidationError):
        SkillResponse.model_validate(d)
    d = build_response("walk", "error", error_code="x", error_message="y")
    d["error"] = None
    with pytest.raises(ValidationError):
        SkillResponse.model_validate(d)


def test_robot_state_tolerates_extra_fields():
    d = build_response("read_state", "ok", state_after={**STATE, "extra_field": 5})
    r = SkillResponse.model_validate(d)
    assert r.state_after.posture == "standing"
