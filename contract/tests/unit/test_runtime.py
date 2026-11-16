"""The skill and utility runtime in process: params parsing, state sampling, response building, run_main
(contract/docs/contract.md). One subprocess case per mechanism stays in tests/integration."""

from __future__ import annotations

import pytest

from contract import env, process, runtime
from contract.runtime import InvalidParams, SkillOutcome, StateSampler, parse_params
from contract.schema import ErrorCode, RobotState

STATE = RobotState(t=1.0, backend="stub", posture="standing", body_height=0.32)


def no_state() -> RobotState:
    raise AssertionError("not sampled")


@pytest.mark.parametrize(
    "argv",
    [["skill", "not json"], ["skill", "[1]"], ["skill", "42"], ["skill"]],
    ids=["not_json", "list", "scalar", "missing"],
)
def test_parse_params_rejects(argv):
    with pytest.raises(InvalidParams):
        parse_params(argv)


def test_parse_params_accepts_object():
    assert parse_params(["skill", '{"a": 1}']) == {"a": 1}


def test_error_code_serialises_as_plain_string():
    outcome = SkillOutcome.error(ErrorCode.SDK_ERROR, "m", observations={}, timing={})
    response = runtime.build_response("walk", outcome, StateSampler(no_state), 1.0)
    assert response.error is not None
    assert type(response.error.code) is str and str(ErrorCode.SDK_ERROR) == "sdk_error"
    assert '"code":"sdk_error"' in runtime.to_json(response)


def test_message_collapsed_not_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    outcome = SkillOutcome.error(ErrorCode.EXCEPTION, msg, observations={}, timing={})
    error = runtime.build_response("walk", outcome, StateSampler(no_state), 1.0).error
    assert error is not None
    assert error.message == "line one line two three " + "x" * 400


def test_run_main_keeps_state_sampled_before_an_exception(monkeypatch):
    """The state taken before the body raised survives into the error response, after the setup timing."""
    monkeypatch.setenv(env.BACKEND, "stub")
    monkeypatch.setattr(process, "capture_stdout", lambda: None)
    sent = []

    def emit(skill, outcome, states, total_ms, setup):
        sent.append(runtime.build_response(skill, outcome, states, total_ms, setup=setup))
        raise SystemExit

    def body(states: StateSampler) -> SkillOutcome:
        states.take("before")
        raise RuntimeError("boom\nsecond line")

    monkeypatch.setattr(runtime, "emit", emit)
    with pytest.raises(SystemExit):
        runtime.run_main("walk", body, states=StateSampler(lambda: STATE), setup=lambda: {"setup_ms": 1.0})
    response = sent[0]
    assert response.error is not None and response.error.message == "RuntimeError: boom"
    assert (response.state_before, response.state_after) == (STATE, None)
    assert list(response.timing) == ["setup_ms", "state_ms", "total_ms"]


def test_state_ms_excludes_prepare(monkeypatch):
    """The prepare step before a sample (a connect or DDS init) is not counted in state_ms."""
    monkeypatch.setenv(env.BACKEND, "stub")
    now = [0.0]

    def read() -> RobotState:
        now[0] += 0.01
        return STATE

    def prepare() -> None:
        now[0] += 1.0

    states = StateSampler(read, prepare=prepare, clock=lambda: now[0])
    states.take("before")
    assert states.used and states.seconds == pytest.approx(0.01) and states.before is STATE
