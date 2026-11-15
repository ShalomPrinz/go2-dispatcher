"""The kill switch: one current process, the first kill cause wins, an exited process is never signalled
(docs/safety.md)."""

from __future__ import annotations

from dispatcher.execution.kill import KillSwitch
from dispatcher.models import StopCause


class FakeProcess:
    def __init__(self, rc: int | None = None):
        self.rc = rc
        self.kills = 0

    def poll(self) -> int | None:
        return self.rc

    def kill(self) -> None:
        self.kills += 1


def test_first_kill_wins_and_is_recorded():
    switch, proc = KillSwitch(), FakeProcess()
    assert switch.kill_current(StopCause.OPERATOR) is False  # nothing registered
    switch.register(proc)
    assert switch.kill_current(StopCause.OPERATOR) is True
    assert switch.kill(proc, StopCause.STEP_TIMEOUT) is False
    rc, kill = switch.poll(proc)
    assert rc is None and kill is not None and kill.cause == StopCause.OPERATOR and proc.kills == 1


def test_an_exited_or_released_process_is_not_signalled():
    switch, exited = KillSwitch(), FakeProcess(rc=0)
    switch.register(exited)
    assert switch.kill_current(StopCause.OPERATOR) is False and exited.kills == 0
    other = FakeProcess()
    switch.register(other)
    assert switch.kill(exited, StopCause.OPERATOR) is False  # no longer current
    switch.release()
    assert switch.kill(other, StopCause.OPERATOR) is False and other.kills == 0
    assert switch.poll(other) == (None, None)
