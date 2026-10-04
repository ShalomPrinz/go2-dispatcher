"""CLI subprocess tests (tests/docs/testing.md): GO2_TEST_PLANNER=planner_factory:factory."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

from dispatcher.registry import Registry
from skills import stub
from tests.helpers import REPO_ROOT

pytestmark = pytest.mark.integration

HELPERS_DIR = REPO_ROOT / "dispatcher" / "tests" / "helpers"
TURN = {"skill": "turn", "params": {"direction": "left", "angle_deg": 90}}
PLAN_TURN = {"status": "PLAN", "steps": [TURN]}
DONE = {"status": "DONE", "steps": [], "message": "Turned left."}
ABORT = {"status": "ABORT", "steps": [], "message": "Cannot do that."}
WAIT_LOCK_S = 10.0
STOP_WITHIN_S = 5.0


@pytest.fixture
def cfg_path(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        f'[skills]\ndir = "{REPO_ROOT / "skills" / "catalog"}"\n'
        '[log]\ndir = "runs"\n'
        '[robot]\nnetwork_interface = "eth0"\n'
        '[stub]\nstate_file = "runs/.stub_state.json"\ntime_scale = 0.01\n',
        encoding="utf-8",
    )
    return p


def cli_env(script=None):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env["PYTHONPATH"] = os.pathsep.join([str(HELPERS_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["GO2_TEST_PLANNER"] = "planner_factory:factory"
    env["GO2_TEST_SCRIPT"] = json.dumps(script or [])
    return env


def argv(cfg_path, *args):
    return [sys.executable, "-m", "dispatcher.transports.cli", "--config", str(cfg_path), *args]


def cli(cfg_path, *args, script=None, timeout=30):
    return subprocess.run(
        argv(cfg_path, *args), env=cli_env(script), cwd=cfg_path.parent, capture_output=True, text=True, timeout=timeout
    )


def test_catalog(cfg_path):
    env = cli_env()
    env.pop("GO2_TEST_PLANNER")
    proc = subprocess.run(
        argv(cfg_path, "catalog"), env=env, cwd=cfg_path.parent, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert Registry.load(REPO_ROOT / "skills" / "catalog").catalog_text() in proc.stdout
    assert re.search(r"^Registry hash: [0-9a-f]{16}$", proc.stdout, re.M)


def test_reset_stub(cfg_path):
    state_file = cfg_path.parent / "runs" / ".stub_state.json"
    stub.write_posture("sitting", state_file)
    proc = cli(cfg_path, "--reset-stub")
    assert proc.returncode == 0, proc.stderr
    assert stub.read_posture(state_file) == "standing"


def test_cli_does_not_import_anthropic():
    """`anthropic` is imported lazily (dispatcher/docs/llm.md); the CLI starts fast without it."""
    code = "import sys, dispatcher.transports.cli; print('anthropic' in sys.modules)"
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False"


def test_run_done(cfg_path):
    proc = cli(cfg_path, "run", "turn left", script=[PLAN_TURN, DONE])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0] == "DONE: Turned left."
    assert lines[1] == "Steps: 1 run, 0 failed"
    assert lines[2].startswith("1. turn(")


def test_run_abort(cfg_path):
    proc = cli(cfg_path, "run", "fly", script=[ABORT])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.startswith("ABORTED: Cannot do that.")


def test_batch(cfg_path, tmp_path):
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("# comment\nturn left\n\nfly\n", encoding="utf-8")
    proc = cli(cfg_path, "batch", str(tasks), script=[PLAN_TURN, DONE, ABORT])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DONE: Turned left." in proc.stdout and "ABORTED: Cannot do that." in proc.stdout


def test_fault_with_real_backend(cfg_path):
    proc = cli(cfg_path, "--fault", "2:hang", "--backend", "real", "run", "x")
    assert proc.returncode == 2
    assert "Config error" in proc.stderr


def test_second_run_is_locked_out_then_sigint_stops(cfg_path):
    log_dir = cfg_path.parent / "runs"
    first = subprocess.Popen(
        argv(cfg_path, "--fault", "1:hang", "run", "turn left"),
        env=cli_env([PLAN_TURN]),
        cwd=cfg_path.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + WAIT_LOCK_S
        while not any(log_dir.glob("*.jsonl")) and time.monotonic() < deadline:
            assert first.poll() is None, first.communicate()
            time.sleep(0.05)
        assert any(log_dir.glob("*.jsonl")), "first run never started"

        second = cli(cfg_path, "run", "turn left", script=[PLAN_TURN, DONE])
        assert second.returncode == 2
        assert "Another dispatcher is running" in second.stderr

        first.send_signal(signal.SIGINT)
        out, err = first.communicate(timeout=STOP_WITHIN_S)
        assert first.returncode == 1, out + err
        assert "Stopping." in out
        assert "STOPPED:" in out
    finally:
        if first.poll() is None:
            first.kill()
            first.wait()
