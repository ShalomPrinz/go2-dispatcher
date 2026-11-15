"""The skill runtime's stub faults in process (skills/docs/skills.md#skill-faults); one subprocess case per kind
stays in tests/integration."""

from __future__ import annotations

import os

import pytest

from contract import env, process
from contract.faults import CRASH_EXIT_CODE
from skills import faults


class Exited(Exception):
    pass


@pytest.fixture
def exits(monkeypatch):
    """Record os._exit codes and raw stdout writes instead of acting."""
    codes: list[int] = []
    out: list[str] = []

    def fake_exit(code):
        codes.append(code)
        raise Exited

    monkeypatch.setattr(os, "_exit", fake_exit)
    monkeypatch.setattr(process, "write_raw_stdout", out.append)
    return codes, out


@pytest.mark.parametrize(
    "kind, code, stdout",
    [("crash", CRASH_EXIT_CODE, []), ("garbage", 0, [faults.GARBAGE_TEXT])],
)
def test_hook_fires_once(monkeypatch, exits, kind, code, stdout):
    monkeypatch.setenv(env.STUB_FAULT, kind)
    f = faults._SkillFaults()
    with pytest.raises(Exited):
        f.fire()
    f.fire()  # already fired: no second exit
    assert exits == ([code], stdout)
    assert not f.injected_error()


def test_error_fires_once_and_not_in_the_hook(monkeypatch, exits):
    monkeypatch.setenv(env.STUB_FAULT, "error")
    f = faults._SkillFaults()
    f.fire()
    assert [f.injected_error(), f.injected_error()] == [True, False]
    assert exits == ([], [])


def test_no_fault_when_unset_and_a_robot_fault_is_refused(monkeypatch, exits):
    monkeypatch.delenv(env.STUB_FAULT, raising=False)
    f = faults._SkillFaults()
    f.fire()
    assert not f.injected_error() and exits == ([], [])
    monkeypatch.setenv(env.STUB_FAULT, "stuck")
    with pytest.raises(ValueError, match="GO2_STUB_FAULT='stuck'"):
        faults._SkillFaults().fire()
