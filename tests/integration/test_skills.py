"""The five skills on the stub backend, with the robot service and an armed lease: contract, stub behaviour,
faults, orphan watchdog (skills/docs/skills.md, tests/docs/testing.md)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from pydantic import TypeAdapter

from skills import stub
from skills.schema import SkillResponse
from tests.helpers import REPO_ROOT, run_module, single_response, stub_env
from tests.integration import robot, vision

pytestmark = pytest.mark.integration

RESPONSE = TypeAdapter(SkillResponse)

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


@pytest.fixture
def svc(tmp_path):
    with robot.running(stub_env(tmp_path)) as service:
        yield service


def run_ok(name, params, env):
    proc = run_module(name, params, env)
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 0, proc.stderr
    assert resp.status == "ok", resp
    return resp


# --- contract -------------------------------------------------------------------


@pytest.mark.parametrize("name", SKILLS)
def test_contract_valid(svc, name):
    with vision.running(svc.env) as env:
        resp = run_ok(name, VALID[name], svc.arm(env))
    assert resp.skill == name and resp.error is None
    assert resp.state_before is not None and resp.state_before.backend == "stub"
    assert resp.state_after is not None and resp.state_after.backend == "stub"
    assert resp.state_error is None
    assert list(resp.timing)[0] == "robot_connect_ms"  # skills never touch DDS
    assert {"exec_ms", "state_ms", "total_ms"} <= set(resp.timing)
    assert not {"dds_init_ms", "client_init_ms"} & set(resp.timing)
    assert ("connect_ms" in resp.timing) == (name == "detect_object")  # the vision service connect


def test_invalid_json(tmp_path):
    """parse_params is tested in process (skills/tests/unit/test_skill_helpers.py); this proves the
    mapping to invalid_params and exit 1."""
    proc = run_module("walk", "not json", stub_env(tmp_path))
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1
    assert resp.status == "error" and resp.error is not None and resp.error.code == "invalid_params"


def test_backend_not_configured(tmp_path):
    env = stub_env(tmp_path)
    env.pop("GO2_BACKEND")
    proc = run_module("walk", VALID["walk"], env)
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1
    assert resp.error is not None
    assert resp.error.code == "backend_not_configured"
    assert "Traceback" not in proc.stderr


def test_fresh_interpreter_import_is_side_effect_free():
    """Every skills module imports without heavy modules or output (skills/docs/skills.md)."""
    code = r"""
import contextlib, importlib, io, json, pkgutil, sys
import skills
names = sorted(m.name for m in pkgutil.iter_modules(skills.__path__))
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    mods = {n: importlib.import_module("skills." + n) for n in names}
heavy = [m for m in ("unitree_sdk2py", "cv2", "ultralytics", "numpy", "cyclonedds")
         if m in sys.modules]
policies = {n: getattr(m, "POLICY", None) and m.POLICY.name for n, m in mods.items()}
print(json.dumps({"modules": names, "heavy": heavy, "out": buf.getvalue(),
                  "policies": policies}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={k: v for k, v in os.environ.items() if not k.startswith("GO2_")},
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert {
        "real",
        "stub",
        "policy",
        "process",
        "runner",
        "backend",
        "motion",
        "stop_move",
        "read_state",
        "vision_ipc",
        "vision_service",
        "ipc",
        "local",
        "robot_ipc",
        "robot_service",
        "state_store",
        "real_detector",
        *SKILLS,
    } <= set(out["modules"])
    assert out["heavy"] == [] and out["out"] == "" and proc.stderr == ""
    for n in SKILLS:
        assert out["policies"][n] == n


# --- observations -----------------------------------------------------------------


def test_walk_observations(svc):
    resp = run_ok("walk", {"direction": "backward", "distance_m": 0.3}, svc.arm())
    assert resp.observations == {"direction": "backward", "distance_m": 0.3, "duration_s": 2.1, "sdk_ret": 0}
    assert resp.state_after is not None and resp.state_after.position == pytest.approx([-0.63, 0.0, 0.32])


def test_turn_observations(svc):
    resp = run_ok("turn", {"direction": "right", "angle_deg": 90}, svc.arm())
    assert resp.observations == {"direction": "right", "angle_deg": 90.0, "duration_s": 1.8, "sdk_ret": 0}


# --- stub behaviour -----------------------------------------------------------------


def test_sit_then_walk_fails(tmp_path, svc):
    resp = run_ok("sit", {}, svc.arm())
    assert resp.observations == {"sdk_ret": 0}
    assert stub.read_posture(state_file(tmp_path)) == "sitting"

    proc = run_module("walk", VALID["walk"], svc.arm())
    walk = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1
    assert walk.error is not None
    assert walk.status == "error" and walk.error.code == "sdk_error"
    assert walk.error.message == f"Move returned {stub.STUB_ERR_NOT_STANDING}"
    assert walk.observations["sdk_ret"] == stub.STUB_ERR_NOT_STANDING
    assert stub.read_posture(state_file(tmp_path)) == "sitting"


def test_sit_while_sitting_is_ok(tmp_path, svc):
    stub.write_posture("sitting", state_file(tmp_path))
    run_ok("sit", {}, svc.arm())


def test_stretch_while_sitting_fails(tmp_path, svc):
    stub.write_posture("sitting", state_file(tmp_path))
    proc = run_module("stretch", {}, svc.arm())
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1
    assert resp.error is not None
    assert resp.error.code == "sdk_error"
    assert resp.error.message == f"Stretch returned {stub.STUB_ERR_NOT_STANDING}"


def test_detect_follows_the_pose(tmp_path):
    """A chair behind the camera at the start is ahead after a left turn (pose kept in the state file)."""
    world = stub_env(tmp_path, objects={"chair": {"x": 0.0, "y": 1.2}})
    with robot.running(world) as svc, vision.running(svc.env) as env:
        assert run_ok("detect_object", {"target": "chair"}, env).observations["object_found"] is False
        turned = run_ok("turn", {"direction": "left", "angle_deg": 90}, svc.arm(env)).state_after
        assert turned is not None and turned.imu_rpy == pytest.approx(
            [0.0, 0.0, 1.8]
        )  # 18 commands of 0.1 rad, lead included
        resp = run_ok("detect_object", {"target": "  Chair "}, env)
    obs = resp.observations
    assert set(obs) == {"target", "object_found", "position", "closeness", "confidence"}
    assert (obs["target"], obs["object_found"], obs["position"]) == ("chair", True, "center")


def test_detect_not_found_is_ok(tmp_path):
    with vision.running(stub_env(tmp_path)) as env:
        resp = run_ok("detect_object", {"target": "bottle"}, env)
    assert resp.observations == {"target": "bottle", "object_found": False}


def test_detect_unsupported_object(tmp_path):
    proc = run_module("detect_object", {"target": "phone"}, stub_env(tmp_path, fault="crash"))
    resp = RESPONSE.validate_python(single_response(proc))  # no detector call was made
    assert proc.returncode == 1
    assert resp.error is not None
    assert resp.error.code == "unsupported_object"
    assert "cell phone" in resp.error.message
    assert resp.error.message.startswith("'phone' is not a detectable object. Closest supported:")


def test_detect_unsupported_without_matches(tmp_path):
    proc = run_module("detect_object", {"target": "xqzv"}, stub_env(tmp_path))
    resp = RESPONSE.validate_python(single_response(proc))
    assert resp.error is not None
    assert resp.error.code == "unsupported_object"
    assert resp.error.message == "'xqzv' is not a detectable object."


def test_detect_without_vision_service(tmp_path):
    env = stub_env(tmp_path, GO2_VISION_SOCKET=str(tmp_path / "none.sock"))
    proc = run_module("detect_object", VALID["detect_object"], env)
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1
    assert resp.error is not None and resp.error.code == "vision_unavailable"
    assert resp.error.message.startswith("VisionUnavailable: no vision service at ")


def test_vision_service_exits_when_parent_dies(tmp_path):
    """The service shares the skills' orphan watchdog (skills/process.py)."""
    env = stub_env(tmp_path, GO2_PARENT_PID=str(os.getppid() or 1), GO2_VISION_SOCKET=str(tmp_path / "v.sock"))
    proc = vision.start(env)
    try:
        assert proc.wait(timeout=5) == 137
    finally:
        vision.stop(proc)


# --- faults --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["walk", "sit", "detect_object"])
def test_fault_error(tmp_path, name):
    """walk: move; sit: single action; detect_object: camera_unavailable. Applied before any service call."""
    with vision.running(stub_env(tmp_path, fault="error")) as env:
        proc = run_module(name, VALID[name], env)
    resp = RESPONSE.validate_python(single_response(proc))
    assert proc.returncode == 1 and resp.status == "error"
    assert resp.error is not None
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
    """The watchdog is shared (skills/process.py); one skill is enough."""
    env = stub_env(tmp_path, fault="hang", GO2_PARENT_PID=str(os.getppid() or 1))
    assert int(env["GO2_PARENT_PID"]) != os.getpid()
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "skills.walk", json.dumps(VALID["walk"])],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert time.monotonic() - t0 < 3.0
    assert proc.returncode == 137
    assert proc.stdout == ""
