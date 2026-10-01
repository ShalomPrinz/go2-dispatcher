"""stop_move and read_state utilities on the stub backend (docs/safety.md, robot.md, testing.md)."""

from __future__ import annotations

import json

import pytest

from dispatcher.models import SkillResponse
from skills import stub
from helpers import run_module, single_response, stub_env

pytestmark = pytest.mark.integration

UTILITIES = ["stop_move", "read_state"]


@pytest.mark.parametrize("name", UTILITIES)
def test_contract_valid(tmp_path, name):
    proc = run_module(name, {}, stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 0, proc.stderr
    assert resp.skill == name and resp.status == "ok"
    assert resp.state_before is None
    assert resp.state_after is not None and resp.state_after.backend == "stub"
    assert resp.state_after.posture == "standing"
    assert resp.timing["total_ms"] >= 0


def test_stop_move_details(tmp_path):
    resp = single_response(run_module("stop_move", {}, stub_env(tmp_path)))
    assert resp["observations"] == {"sdk_ret": 0}
    assert {"stop_call_ms", "state_ms", "total_ms"} <= set(resp["timing"])
    assert resp["timing"]["stop_call_ms"] <= resp["timing"]["total_ms"]


@pytest.mark.parametrize("name", UTILITIES)
def test_reports_sitting_from_state_file(tmp_path, name):
    env = stub_env(tmp_path)
    stub.write_posture("sitting", tmp_path / "stub_state.json")
    resp = single_response(run_module(name, {}, env))
    assert resp["state_after"]["posture"] == "sitting"
    assert resp["state_after"]["body_height"] == stub.BODY_HEIGHT_SITTING_M


@pytest.mark.parametrize("name", UTILITIES)
def test_invalid_params(tmp_path, name):
    proc = run_module(name, "not json", stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.status == "error" and resp.error.code == "invalid_params"


@pytest.mark.parametrize("name", UTILITIES)
def test_backend_not_configured(tmp_path, name):
    """The three bad values are tested in process (skills/tests/unit/test_skill_helpers.py)."""
    env = stub_env(tmp_path, GO2_BACKEND="simulator")
    proc = run_module(name, {}, env)
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.error.code == "backend_not_configured"
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("name", UTILITIES)
def test_noise_goes_to_stderr(tmp_path, name):
    proc = run_module(name, {}, stub_env(tmp_path, GO2_STUB_NOISE="1"))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 0 and resp.status == "ok"
    assert stub.NOISE_PRINT_TEXT in proc.stderr
    assert stub.NOISE_RAW_TEXT.decode().strip() in proc.stderr


@pytest.mark.parametrize("name", UTILITIES)
def test_utilities_ignore_faults(tmp_path, name):
    """One `os.environ.pop` covers every kind; hang is the one whose failure is dangerous."""
    proc = run_module(name, {}, stub_env(tmp_path, fault="hang"), timeout=10)
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 0 and resp.status == "ok"


def test_stub_state_file_missing_parent_is_created(tmp_path):
    path = tmp_path / "a" / "b" / "state.json"
    stub.write_posture("sitting", path)
    assert stub.read_posture(path) == "sitting"
    assert json.loads(path.read_text()) == {"posture": "sitting"}
    assert stub.read_posture(tmp_path / "nope.json") == "standing"

