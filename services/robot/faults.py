"""Stub fault injection (services/robot/docs/service.md): the skill process's side (inject_fault) and the robot
service's side of a service fault; the fault kinds are in contract.faults. Standard library and contract only."""

from __future__ import annotations

import os
import signal
import sys
import time

from contract import process
from contract.env import STUB_FAULT
from contract.faults import CRASH_EXIT_CODE, FAULT_KINDS

GARBAGE_TEXT = "not json\n"
HANG_POLL_S = 1.0


class _FaultInjector:
    """The skill process's fault state: only its first action call is faulted."""

    def __init__(self) -> None:
        self.consumed = False

    def apply_fault(self) -> str | None:
        """Apply the injected fault to this call. May hang forever, exit the process or write to stdout;
        otherwise returns ``None``, ``"error"`` or a service fault for the robot service to apply."""
        if self.consumed:
            return None
        self.consumed = True
        kind = os.environ.get(STUB_FAULT) or None
        if kind is not None and kind not in FAULT_KINDS:
            raise ValueError(f"{STUB_FAULT}={kind!r} is not one of {', '.join(FAULT_KINDS)}")
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
        return kind  # None, "error" or a service fault


_faults = _FaultInjector()


def inject_fault() -> str | None:
    """A skill's action call for fault injection, in the skill process before it asks a service."""
    return _faults.apply_fault()


def apply_service_fault(kind: str) -> None:
    """The robot service's side of a service fault: hang (the whole process stops, sockets stay open) or crash."""
    print(f"stub fault: {kind}", file=sys.stderr, flush=True)
    if kind == "service_crash":
        os._exit(CRASH_EXIT_CODE)
    os.kill(os.getpid(), signal.SIGSTOP)
