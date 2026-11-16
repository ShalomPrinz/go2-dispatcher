"""The backend loader (services/robot/docs/service.md#robot-backends): one object per process, chosen by GO2_BACKEND."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from contract.backend import BackendNotConfigured
from services.robot import backends
from services.robot.backends.sim import SimBackend


def test_stub_loads_the_sim_backend_once(monkeypatch):
    monkeypatch.setenv("GO2_BACKEND", "stub")
    robot = backends.load()
    assert isinstance(robot, SimBackend) and robot.name == "stub" and backends.load() is robot


@pytest.mark.parametrize("name", ["", "sim"])
def test_unknown_backend_is_not_configured(monkeypatch, name):
    monkeypatch.setenv("GO2_BACKEND", name)
    with pytest.raises(BackendNotConfigured):
        backends.load()
    assert backends.setup_timing() == {}


STUB_MODULES = (
    "unitree_sdk2py",
    "services.robot.backends.sim",
    "services.robot.backends.faults",
    "services.backend.stub_state",
    "contract.stub",
    "contract.faults",
)


def test_real_loads_without_the_sdk_the_sim_or_fault_code():
    """In a fresh process: load() on real, and the robot service module, import neither the SDK nor any stub code."""
    code = (
        "import json, sys; from services.robot import backends, service; r = backends.load(); "
        f"stub = sorted(m for m in sys.modules if m.startswith({STUB_MODULES!r})); "
        "print(json.dumps([type(r).__name__, r.name, stub]))"
    )
    env = {"GO2_BACKEND": "real", "PATH": ""}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == ["RealBackend", "real", []]
