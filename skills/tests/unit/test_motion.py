"""SDK-calling skill code in process with a fake sport client: the motion loop, single
actions and the stop_move utility (skills/docs/skills.md, docs/safety.md). The stub's faults
only fail the first SDK call, so the mid-loop and cleanup paths are covered here."""

from __future__ import annotations

import sys

import pytest

from skills import backend, motion, result, stop_move

PERIOD = 0.1


class FakeClient:
    """Sport client returning scripted codes. ``move`` items: int (returned) or an
    exception instance (raised); after the list runs out Move returns 0."""

    def __init__(self, move=(), stop=0, stand_down=0):
        self.move = list(move)
        self.stop = stop
        self.stand_down = stand_down
        self.calls: list[str] = []

    def Move(self, vx, vy, vyaw):
        self.calls.append("Move")
        item = self.move.pop(0) if self.move else 0
        if isinstance(item, BaseException):
            raise item
        return item

    def StopMove(self):
        self.calls.append("StopMove")
        if isinstance(self.stop, BaseException):
            raise self.stop
        return self.stop

    def StandDown(self):
        self.calls.append("StandDown")
        return self.stand_down


@pytest.fixture
def sdk(monkeypatch):
    """Install a FakeClient (returned by ``sdk(client)``) and record backend sleeps."""
    sleeps: list[float] = []
    monkeypatch.setattr(backend, "sleep", sleeps.append)
    monkeypatch.setattr(result, "ORPHANED", False)

    def install(client):
        monkeypatch.setattr(backend, "get_sport_client", lambda: client)
        return sleeps

    return install


# --- move_loop -----------------------------------------------------------------------


@pytest.mark.parametrize("stop, suffix", [(0, ""), (5, "; StopMove returned 5")], ids=["stop_ok", "stop_fails"])
def test_move_fails_mid_loop(sdk, stop, suffix):
    client = FakeClient(move=[0, 0, 7], stop=stop)
    sdk(client)
    o = motion.move_loop(0.3, 0, 0, 1.0, PERIOD)
    assert (o.status, o.error_code, o.error_message) == ("error", "sdk_error", "Move returned 7" + suffix)
    assert o.observations == {"duration_s": round(2 * PERIOD, 3), "sdk_ret": 7}
    assert client.calls == ["Move"] * 3 + ["StopMove"]


def test_final_stop_move_fails(sdk):
    client = FakeClient(stop=4)
    sdk(client)
    o = motion.move_loop(0.3, 0, 0, 0.3, PERIOD)
    assert (o.status, o.error_code, o.error_message) == ("error", "sdk_error", "StopMove returned 4")
    assert o.observations == {"duration_s": round(3 * PERIOD, 3), "sdk_ret": 4}
    assert client.calls == ["Move"] * 3 + ["StopMove"]


@pytest.mark.parametrize("stop", [0, RuntimeError("stop broke")], ids=["stop_ok", "stop_raises"])
def test_move_raises_still_stops(sdk, stop):
    """The Move exception propagates; _safe_stop swallows a second one from StopMove."""
    client = FakeClient(move=[0, ValueError("dds down")], stop=stop)
    sdk(client)
    with pytest.raises(ValueError, match="dds down"):
        motion.move_loop(0.3, 0, 0, 1.0, PERIOD)
    assert client.calls == ["Move", "Move", "StopMove"]


def test_orphaned_breaks_and_stops(sdk, monkeypatch):
    client = FakeClient()
    sdk(client)
    monkeypatch.setattr(result, "ORPHANED", True)
    o = motion.move_loop(0.3, 0, 0, 1.0, PERIOD)
    assert (o.status, o.error_code) == ("ok", None)
    assert o.observations == {"orphaned": True, "duration_s": 0.0, "sdk_ret": 0}
    assert client.calls == ["StopMove"]


# --- single_action ---------------------------------------------------------------------


def test_single_action_failure_skips_settle(sdk):
    sleeps = sdk(FakeClient(stand_down=3))
    o = motion.single_action("StandDown", 2.0)
    assert (o.status, o.observations, o.error_code, o.error_message) == (
        "error",
        {"sdk_ret": 3},
        "sdk_error",
        "StandDown returned 3",
    )
    assert sleeps == []


def test_single_action_ok_settles(sdk):
    sleeps = sdk(FakeClient())
    assert motion.single_action("StandDown", 2.0).status == "ok" and sleeps == [2.0]


# --- stop_move utility -----------------------------------------------------------------


class Emitted(Exception):
    """Raised by the patched result.emit instead of exiting the process."""


@pytest.fixture
def run_stop_move(sdk, monkeypatch):
    """Run stop_move.main() in process; returns the SkillResponse it would emit."""

    def run(argv, client, state=None):
        sdk(client)

        def sample():
            if isinstance(state, BaseException):
                raise state
            return state or {"posture": "standing"}

        monkeypatch.setattr(backend, "sample_state", sample)
        monkeypatch.setattr(result, "capture_stdout", lambda: None)
        monkeypatch.setattr(sys, "argv", ["stop_move", *argv])
        emitted = []

        def emit(skill, outcome, states, total_ms):
            emitted.append(result.build_response(skill, outcome, states, total_ms))
            raise Emitted

        monkeypatch.setattr(result, "emit", emit)
        with pytest.raises(Emitted):
            stop_move.main()
        return emitted[0]

    return run


def test_stop_move_invalid_params_still_stops(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["not json"], client)
    assert client.calls == ["StopMove"]
    assert out.error is not None and out.error.code == "invalid_params"
    assert out.observations == {"sdk_ret": 0} and list(out.timing) == ["stop_call_ms", "state_ms", "total_ms"]


def test_stop_move_sdk_error(run_stop_move):
    out = run_stop_move(["{}"], FakeClient(stop=9))
    assert out.error is not None and (out.error.code, out.error.message) == ("sdk_error", "StopMove returned 9")


def test_stop_move_state_error_keeps_ok(run_stop_move):
    client = FakeClient()
    out = run_stop_move(["{}"], client, state=backend.StateUnavailable("no message"))
    assert client.calls == ["StopMove"]
    assert (out.status, out.error, out.state_after) == ("ok", None, None)
    assert out.state_error == "after: StateUnavailable: no message"
