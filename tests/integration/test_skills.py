"""The five skills on the stub backend: contract, stub behaviour, faults, orphan
watchdog (docs/skills.md, testing.md)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from go2_dispatcher.models import SkillResponse
from go2_skills import stub
from helpers import REPO_ROOT, run_module, single_response, stub_env

pytestmark = pytest.mark.integration

VALID = {
    "walk": {"direction": "forward", "distance_m": 0.3},
    "turn": {"direction": "left", "angle_deg": 30},
    "sit": {},
    "stretch": {},
    "detect_object": {"target": "chair"},
}
SKILLS = list(VALID)
def state_file(tmp_path):
    return tmp_path / "stub_state.json"


def run_ok(name, params, env):
    proc = run_module(name, params, env)
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 0, proc.stderr
    assert resp.status == "ok", resp
    return resp


# --- contract -------------------------------------------------------------------


@pytest.mark.parametrize("name", SKILLS)
def test_contract_valid(tmp_path, name):
    resp = run_ok(name, VALID[name], stub_env(tmp_path))
    assert resp.skill == name and resp.error is None
    assert resp.state_before is not None and resp.state_before.backend == "stub"
    assert resp.state_after is not None and resp.state_after.backend == "stub"
    assert resp.state_error is None
    assert {"init_ms", "exec_ms", "state_ms", "total_ms"} <= set(resp.timing)


def test_invalid_json(tmp_path):
    """parse_params is tested in process (tests/unit/test_skill_helpers.py); this proves the
    mapping to invalid_params and exit 1."""
    proc = run_module("walk", "not json", stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.status == "error" and resp.error.code == "invalid_params"


def test_bad_param_value(tmp_path):
    proc = run_module("walk", {"direction": "up", "distance_m": 1}, stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.error.code == "invalid_params"


def test_backend_not_configured(tmp_path):
    env = stub_env(tmp_path)
    env.pop("GO2_BACKEND")
    proc = run_module("walk", VALID["walk"], env)
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.error.code == "backend_not_configured"
    assert "Traceback" not in proc.stderr


def test_noise_stays_off_stdout(tmp_path):
    proc = run_module("walk", VALID["walk"], stub_env(tmp_path, GO2_STUB_NOISE="1"))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 0 and resp.status == "ok"
    assert stub.NOISE_PRINT_TEXT in proc.stderr


def test_fresh_interpreter_import_is_side_effect_free():
    """Every go2_skills module imports without heavy modules or output (docs/skills.md)."""
    code = r"""
import contextlib, importlib, io, json, pkgutil, sys
import go2_skills
names = sorted(m.name for m in pkgutil.iter_modules(go2_skills.__path__))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mods = {n: importlib.import_module("go2_skills." + n) for n in names}
heavy = [m for m in ("unitree_sdk2py", "cv2", "ultralytics", "numpy", "cyclonedds")
         if m in sys.modules]
policies = {n: getattr(m, "POLICY", None) and m.POLICY.name for n, m in mods.items()}
print(json.dumps({"modules": names, "heavy": heavy, "out": buf.getvalue(),
                  "policies": policies}))
"""
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                          text=True, timeout=30, env={k: v for k, v in os.environ.items()
                                                      if not k.startswith("GO2_")})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert {"real", "stub", "result", "backend", "motion", "stop_move", "read_state",
            *SKILLS} <= set(out["modules"])
    assert out["heavy"] == [] and out["out"] == "" and proc.stderr == ""
    for n in SKILLS:
        assert out["policies"][n] == n


# --- observations -----------------------------------------------------------------


def test_walk_observations(tmp_path):
    resp = run_ok("walk", {"direction": "backward", "distance_m": 0.3}, stub_env(tmp_path))
    assert resp.observations == {"direction": "backward", "distance_m": 0.3,
                                 "duration_s": 1.0, "sdk_ret": 0}


def test_turn_observations(tmp_path):
    resp = run_ok("turn", {"direction": "right", "angle_deg": 90}, stub_env(tmp_path))
    assert resp.observations == {"direction": "right", "angle_deg": 90.0,
                                 "duration_s": 1.6, "sdk_ret": 0}


# --- stub behaviour -----------------------------------------------------------------


def test_sit_then_walk_fails(tmp_path):
    env = stub_env(tmp_path)
    resp = run_ok("sit", {}, env)
    assert resp.observations == {"sdk_ret": 0}
    assert resp.state_before.posture == "standing"
    assert resp.state_after.posture == "sitting"
    assert stub.read_posture(state_file(tmp_path)) == "sitting"

    proc = run_module("walk", VALID["walk"], env)
    walk = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert walk.status == "error" and walk.error.code == "sdk_error"
    assert walk.error.message == f"Move returned {stub.STUB_ERR_NOT_STANDING}"
    assert walk.observations["sdk_ret"] == stub.STUB_ERR_NOT_STANDING
    assert stub.read_posture(state_file(tmp_path)) == "sitting"


def test_sit_while_sitting_is_ok(tmp_path):
    stub.write_posture("sitting", state_file(tmp_path))
    run_ok("sit", {}, stub_env(tmp_path))


def test_stretch_while_sitting_fails(tmp_path):
    stub.write_posture("sitting", state_file(tmp_path))
    proc = run_module("stretch", {}, stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1
    assert resp.error.code == "sdk_error"
    assert resp.error.message == f"Stretch returned {stub.STUB_ERR_NOT_STANDING}"


def test_detect_found(tmp_path):
    env = stub_env(tmp_path, detections={"chair": "left:far"})
    resp = run_ok("detect_object", {"target": "  Chair "}, env)
    assert resp.observations == {"target": "chair", "object_found": True,
                                 "position": "left", "closeness": "far", "confidence": 0.9}


def test_detect_not_found_is_ok(tmp_path):
    resp = run_ok("detect_object", {"target": "bottle"}, stub_env(tmp_path))
    assert resp.observations == {"target": "bottle", "object_found": False}


def test_detect_unsupported_object(tmp_path):
    proc = run_module("detect_object", {"target": "phone"}, stub_env(tmp_path, fault="crash"))
    resp = SkillResponse.model_validate(single_response(proc))  # no detector call was made
    assert proc.returncode == 1
    assert resp.error.code == "unsupported_object"
    assert "cell phone" in resp.error.message
    assert resp.error.message.startswith("'phone' is not a detectable object. Closest supported:")


def test_detect_unsupported_without_matches(tmp_path):
    proc = run_module("detect_object", {"target": "xqzv"}, stub_env(tmp_path))
    resp = SkillResponse.model_validate(single_response(proc))
    assert resp.error.code == "unsupported_object"
    assert resp.error.message == "'xqzv' is not a detectable object."


# --- faults --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["walk", "sit", "detect_object"])
def test_fault_error(tmp_path, name):
    """walk: move loop; sit: single action; detect_object: camera_unavailable."""
    proc = run_module(name, VALID[name], stub_env(tmp_path, fault="error"))
    resp = SkillResponse.model_validate(single_response(proc))
    assert proc.returncode == 1 and resp.status == "error"
    if name == "detect_object":
        assert resp.error.code == "camera_unavailable"
    else:
        assert resp.error.code == "sdk_error"
        assert resp.observations["sdk_ret"] == stub.STUB_ERR_INJECTED


def test_fault_crash(tmp_path):
    proc = run_module("walk", VALID["walk"], stub_env(tmp_path, fault="crash"))
    assert proc.returncode == stub.CRASH_EXIT_CODE
    assert proc.stdout == ""


def test_fault_garbage(tmp_path):
    proc = run_module("walk", VALID["walk"], stub_env(tmp_path, fault="garbage"))
    assert proc.returncode == 0
    assert proc.stdout == stub.GARBAGE_TEXT


# --- orphan watchdog -------------------------------------------------------------------


def test_orphan_watchdog_exits(tmp_path):
    """The watchdog is shared (go2_skills/result.py); one skill is enough."""
    env = stub_env(tmp_path, fault="hang", GO2_PARENT_PID=str(os.getppid() or 1))
    assert int(env["GO2_PARENT_PID"]) != os.getpid()
    t0 = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "go2_skills.walk", json.dumps(VALID["walk"])],
                          env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
    assert time.monotonic() - t0 < 3.0
    assert proc.returncode == 137
    assert proc.stdout == ""


@pytest.mark.parametrize("name,params", [("walk", {"direction": "left", "distance_m": 3.0}),
                                         ("turn", {"direction": "right", "angle_deg": 180})])
def test_orphaned_motion_loop_breaks_and_stops(tmp_path, name, params):
    env = stub_env(tmp_path, GO2_STUB_TIME_SCALE="1", GO2_PARENT_PID=str(os.getppid() or 1))
    t0 = time.monotonic()
    proc = run_module(name, params, env, timeout=10)
    resp = SkillResponse.model_validate(single_response(proc))
    assert time.monotonic() - t0 < 3.0
    assert resp.status == "ok"
    assert resp.observations["orphaned"] is True
    assert resp.observations["sdk_ret"] == 0
    assert resp.observations["duration_s"] < 2.0
