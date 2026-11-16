"""The stop_move utility in process with a fake sport client (docs/safety.md)."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from contract import process, runtime
from services.robot import backends, protocol, stop_move
from services.robot.protocol import StateUnavailable
from services.robot.tests.fake_sport import FakeClient


class Emitted(Exception):
    """Raised by the patched runtime.emit instead of exiting the process."""


@pytest.fixture
def run_stop_move(monkeypatch):
    """Run stop_move.main() in process with its own fake sport client; returns the SkillResponse it would emit."""

    def run(argv, client, state=None):
        def sample():
            if isinstance(state, BaseException):
                raise state
            return state or {"posture": "standing"}

        robot = SimpleNamespace(
            init_dds=lambda: None,
            setup_timing=lambda: {"dds_init_ms": 1.0},
            sport_client=lambda: client,
            sample_state=sample,
            sleep=lambda s: None,
        )
        monkeypatch.setenv("GO2_BACKEND", "stub")
        monkeypatch.setattr(backends, "load", lambda: robot)
        monkeypatch.setattr(process, "capture_stdout", lambda: None)
        monkeypatch.setattr(sys, "argv", ["stop_move", *argv])
        emitted = []

        def emit(skill, outcome, states, total_ms, setup):
            emitted.append(runtime.build_response(skill, outcome, states, total_ms, setup=setup))
            raise Emitted

        monkeypatch.setattr(runtime, "emit", emit)
        with pytest.raises(Emitted):
            stop_move.main()
        return emitted[0]

    return run


def test_stop_move_invalid_params_still_stops(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["not json"], client)
    assert client.calls == ["StopMove"]
    assert out.error is not None and out.error.code == "invalid_params"
    assert out.observations == {"sdk_ret": 0} and list(out.timing) == [
        "dds_init_ms",
        "client_init_ms",
        "stop_call_ms",
        "state_ms",
        "total_ms",
    ]


def test_stop_move_sdk_error(run_stop_move):
    out = run_stop_move(["{}"], FakeClient(stop=9))
    assert out.error is not None and (out.error.code, out.error.message) == ("sdk_error", "StopMove returned 9")


def test_stop_move_state_error_keeps_ok(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["{}"], client, state=StateUnavailable("no message"))
    assert client.calls == ["StopMove"]
    assert (out.status, out.error, out.state_after) == ("ok", None, None)
    assert out.state_error == "after: StateUnavailable: no message"


def test_utility_descriptors_name_their_modules():
    """The dispatcher runs the utilities by protocol descriptor; each names its module and is that module's POLICY."""
    for utility in (protocol.STOP_MOVE, protocol.READ_STATE):
        module = importlib.import_module(utility.module)
        assert module.POLICY is utility.policy
        assert utility.policy.name == utility.module.rsplit(".", 1)[1]
