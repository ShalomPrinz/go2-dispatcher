"""The sim's robot faults (services/robot/docs/service.md#fault-injection): FaultySportClient wraps the robot
service's SimRobot and applies the robot fault the sim backend arms it with. Only the sim backend imports it."""

from __future__ import annotations

import os
import signal
import sys
import threading
from pathlib import Path
from typing import cast

from contract.faults import CRASH_EXIT_CODE, STUB_ERR_INJECTED, RobotFault, take_fault
from contract.stub import CONDITIONS, Condition
from services.robot.backends.sim import SimRobot

# The rejected fault's codes, the Unitree SDK's (unitree_sdk2py/rpc/internal.py): Move is sent without a reply, so a
# failed send is the only non-zero code it returns; actions wait for a reply and time out.
SDK_ERR_CLIENT_SEND = 3102
SDK_ERR_API_TIMEOUT = 3104


def apply_process_fault(kind: RobotFault) -> None:
    """service_hang (the whole process stops, sockets stay open) or service_crash, from the calling thread."""
    print(f"stub fault: {kind}", file=sys.stderr, flush=True)
    if kind == "service_crash":
        os._exit(CRASH_EXIT_CODE)
    os.kill(os.getpid(), signal.SIGSTOP)


class FaultySportClient:
    """SimRobot with the armed robot fault: it fires once, after the lease's first Move if that returned 0 (mid-walk,
    the command set) or before its first action; rejected and stop_error then fail calls until the next arm."""

    def __init__(self, inner: SimRobot) -> None:
        self.inner = inner
        self._lock = threading.Lock()
        self._pending: RobotFault | None = None  # armed, not yet fired; dropped at the next arm
        self._rejected = False  # every Move and action fails with the SDK's code, without reaching the sim
        self._stop_error = False  # every StopMove fails with STUB_ERR_INJECTED, without reaching the sim

    def clear(self) -> None:
        """The lease is ending: drop the sticky faults and any fault not fired, so its StopMove goes through."""
        with self._lock:
            self._pending, self._rejected, self._stop_error = None, False, False

    def arm(self, kind: RobotFault | None) -> None:
        """A new lease: clear, and arm ``kind`` for it."""
        with self._lock:
            self._pending, self._rejected, self._stop_error = kind, False, False

    def _take(self) -> RobotFault | None:
        with self._lock:
            kind, self._pending = self._pending, None
            return kind

    def _fire(self, kind: RobotFault | None) -> None:
        if kind == "rejected":
            with self._lock:
                self._rejected = True
        elif kind == "stop_error":
            with self._lock:
                self._stop_error = True
        elif kind in CONDITIONS:
            self.inner.inject(cast(Condition, kind))
        elif kind is not None:
            apply_process_fault(kind)

    def Move(self, vx: float, vy: float, vyaw: float) -> int:
        if self._rejected:
            return SDK_ERR_CLIENT_SEND
        kind = self._take()
        ret = self.inner.Move(vx, vy, vyaw)
        if ret == 0:
            self._fire(kind)
        return ret

    def StopMove(self) -> int:
        return STUB_ERR_INJECTED if self._stop_error else self.inner.StopMove()

    def _action(self, name: str) -> int:
        self._fire(self._take())
        return SDK_ERR_API_TIMEOUT if self._rejected else getattr(self.inner, name)()

    def StandDown(self) -> int:
        return self._action("StandDown")

    def Stretch(self) -> int:
        return self._action("Stretch")


class FaultFileArm:
    """The sim backend's arm hook: clear the wrapper before the old lease's StopMove, then arm it with the fault
    file's robot fault (BadFaultFile, a ValueError, refuses the arm)."""

    def __init__(self, client: FaultySportClient, state_file: Path) -> None:
        self._client = client
        self._state_file = state_file

    def clear(self) -> None:
        self._client.clear()

    def arm(self) -> None:
        self._client.arm(take_fault(self._state_file))
