"""Shared skill helpers in process: policy, params parsing, backend selection, stdout capture
(skills/docs/skills.md).
One subprocess case per mechanism stays in tests/integration."""

from __future__ import annotations

import os
import sys

import pytest

from skills import backend, env, result, sit, walk
from skills.result import InvalidParams, MotionCost, parse_params
from skills.schema import ErrorCode


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


@pytest.mark.parametrize("value", [None, "", "simulator"], ids=["unset", "empty", "unknown"])
def test_backend_not_configured(monkeypatch, value):
    monkeypatch.delenv(env.BACKEND, raising=False)
    if value is not None:
        monkeypatch.setenv(env.BACKEND, value)
    with pytest.raises(backend.BackendNotConfigured):
        backend.backend_name()


def test_error_code_serialises_as_plain_string():
    response = result.build_response("walk", "error", error_code=ErrorCode.SDK_ERROR, error_message="m")
    assert response.error is not None
    assert type(response.error.code) is str and str(ErrorCode.SDK_ERROR) == "sdk_error"
    assert '"code":"sdk_error"' in result.to_json(response)


def test_capture_stdout_keeps_junk_off_the_response_line(tmp_path, monkeypatch):
    """Junk via print() and os.write(1) after capture_stdout() goes to stderr; stdout holds only the line."""
    out_path, err_path = tmp_path / "stdout", tmp_path / "stderr"
    line = result.to_json(result.build_response("walk", "ok")) + "\n"
    saved = os.dup(1), os.dup(2)
    monkeypatch.setattr(result, "_saved_stdout_fd", None)
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        try:
            result.capture_stdout()
            with open(1, "w", closefd=False) as fd1:
                monkeypatch.setattr(sys, "stdout", fd1)
                print("junk via print()", flush=True)
            os.write(1, b"junk via os.write(1)\n")
            result.write_raw_stdout(line)
        finally:
            assert result._saved_stdout_fd is not None
            os.close(result._saved_stdout_fd)
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
    assert out_path.read_text() == line
    assert err_path.read_text() == "junk via print()\njunk via os.write(1)\n"
