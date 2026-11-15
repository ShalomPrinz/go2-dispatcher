"""Skill faults of the stub (skills/docs/skills.md): run_skill fires hang, crash or garbage before the body, and
the motion and detection helpers ask injected_error() for error. The kinds are in contract.faults."""

from __future__ import annotations

import os
import sys
import time

from contract import process
from contract.env import STUB_FAULT
from contract.faults import CRASH_EXIT_CODE, SKILL_FAULTS, SkillFault

GARBAGE_TEXT = "not json\n"
HANG_POLL_S = 1.0


class _SkillFaults:
    """This process's skill fault, read from GO2_STUB_FAULT; each kind fires at most once."""

    def __init__(self) -> None:
        self.fired = False
        self.error_consumed = False

    def kind(self) -> SkillFault | None:
        kind = os.environ.get(STUB_FAULT) or None
        if kind is not None and kind not in SKILL_FAULTS:
            raise ValueError(f"{STUB_FAULT}={kind!r} is not one of {', '.join(SKILL_FAULTS)}")
        return kind

    def fire(self) -> None:
        """Apply a process fault: hang forever, crash or write garbage and exit. No-op for error or no fault."""
        if self.fired:
            return
        self.fired = True
        kind = self.kind()
        if kind == "hang":
            print("stub fault: hang", file=sys.stderr, flush=True)
            while True:
                time.sleep(HANG_POLL_S)  # unscaled, forever
        if kind == "crash":
            print("stub fault: crash", file=sys.stderr, flush=True)
            os._exit(CRASH_EXIT_CODE)
        if kind == "garbage":
            print("stub fault: garbage", file=sys.stderr, flush=True)
            process.write_raw_stdout(GARBAGE_TEXT)
            os._exit(0)

    def injected_error(self) -> bool:
        """True once if this process's fault is error: the caller returns its own error response instead of acting."""
        if self.error_consumed or self.kind() != "error":
            return False
        self.error_consumed = True
        return True


_faults = _SkillFaults()


def fire() -> None:
    """run_skill's fault hook, before the body."""
    _faults.fire()


def injected_error() -> bool:
    """A helper's error check, before it asks a service: True once if the step's fault is error."""
    return _faults.injected_error()
