"""Shared skill helpers in process: policy, params parsing and checks, backend selection (skills/docs/skills.md).
One subprocess case per mechanism stays in tests/integration."""

from __future__ import annotations

import math

import pytest

from skills import backend, detect_object, sit, walk
from skills.motion import require_enum, require_number
from skills.result import InvalidParams, MotionCost, parse_params


@pytest.mark.parametrize(
    "argv",
    [["skill", "not json"], ["skill", "[1]"], ["skill", "42"], ["skill"]],
    ids=["not_json", "list", "scalar", "missing"],
)
def test_parse_params_rejects(argv):
    with pytest.raises(InvalidParams):
        parse_params(argv)


def test_policy_resolves_constants_and_functions():
    p = {"direction": "forward", "distance_m": 1.5}
    assert walk.POLICY.timeout_s(p) == walk.BASE_S + walk.FACTOR * 1.5 / walk.VELOCITY_MPS
    assert walk.POLICY.motion_cost(p) == MotionCost(distance_m=1.5)
    assert sit.POLICY.timeout_s({}) == sit.TIMEOUT_S
    assert sit.POLICY.motion_cost({}) == MotionCost()
    assert type(MotionCost(rotation_deg=90).rotation_deg) is float


def test_parse_params_accepts_object():
    assert parse_params(["skill", '{"a": 1}']) == {"a": 1}


@pytest.mark.parametrize(
    "params",
    [{}, {"d": 3}, {"d": True}, {"d": "up"}, {"d": None}],
    ids=["missing", "wrong_type", "bool", "not_in_enum", "none"],
)
def test_require_enum_rejects(params):
    with pytest.raises(InvalidParams):
        require_enum(params, "d", ("left", "right"))


@pytest.mark.parametrize(
    "params",
    [{}, {"x": "1"}, {"x": True}, {"x": math.nan}, {"x": math.inf}, {"x": None}],
    ids=["missing", "wrong_type", "bool", "nan", "inf", "none"],
)
def test_require_number_rejects(params):
    with pytest.raises(InvalidParams):
        require_number(params, "x")


def test_require_accepts_valid():
    assert require_enum({"d": "left"}, "d", ("left", "right")) == "left"
    assert require_number({"x": 3}, "x") == 3.0


@pytest.mark.parametrize(
    "params", [{}, {"target": ""}, {"target": "  "}, {"target": 3}], ids=["missing", "empty", "blank", "not_string"]
)
def test_detect_object_target_rejected(params):
    with pytest.raises(InvalidParams):
        detect_object.body(params)


@pytest.mark.parametrize("value", [None, "", "simulator"], ids=["unset", "empty", "unknown"])
def test_backend_not_configured(monkeypatch, value):
    monkeypatch.delenv(backend.BACKEND_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(backend.BACKEND_ENV, value)
    with pytest.raises(backend.BackendNotConfigured):
        backend.backend_name()
