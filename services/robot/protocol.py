"""The robot service protocol (services/robot/docs/service.md): JSON messages on persistent connections, encoded as
contract/wire.py does, and the service's lifecycle constants. Standard library, contract and
services.robot.state_store only;
imported by the service, by its client (services/robot/client.py), by skills and by the dispatcher."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from contract.constants import Link
from contract.schema import RobotState
from services.robot.state_store import Reading

SERVICE_MODULE = "services.robot.service"  # the ``python -m`` target; the dispatcher starts it without importing it
READY_LINE = "ready"
STARTUP_TIMEOUT_S = 10.0  # SDK import, DDS init and subscription on the lab machine (tunable)
STOP_GRACE_S = 2.0  # SIGTERM to SIGKILL (tunable)
EXIT_LOCKED = 3

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16384
REPLY_WAIT_S = 2.0  # reply wait for hello and subscribe, and added to a state read's own wait (tunable)
MAX_STATE_WAIT_S = 5.0  # the service caps a state read's wait_ms at this
MIN_STREAM_PERIOD_S = 0.02  # the service raises a shorter subscribe period_ms to this
CMD_PERIOD_S = 0.1  # Move period of the service's motion loop (10 Hz)
SPORT_CLIENT_TIMEOUT_S = 5.0  # SDK reply wait of the service's sport client (tunable, services/robot/docs/service.md)
MAX_TIMEOUT_S = 600.0  # the longest lease an arm may ask for
ACTIONS = ("StandDown", "Stretch")  # the sport client actions the service runs as primitives
ACTION_WAIT_S = SPORT_CLIENT_TIMEOUT_S + REPLY_WAIT_S  # a skill's wait for an action reply

ROLE_CONTROL = "control"

ERR_PROTOCOL_VERSION = "protocol_version"
ERR_BAD_REQUEST = "bad_request"
ERR_BUSY = "busy"  # a control connection is already open
ERR_NO_LEASE = "no_lease"  # motion without the armed step's lease
ERR_ABORTED = "aborted"  # the lease ended while the primitive ran
ERR_SDK_EXCEPTION = "sdk_exception"  # the sport client raised
CLOSING_ERRORS = (ERR_PROTOCOL_VERSION, ERR_BAD_REQUEST, ERR_BUSY)  # the service closes the connection after these


class StateUnavailable(Exception):
    """No robot state could be sampled."""


def message(op: str, **fields: Any) -> dict:
    return {"v": PROTOCOL_VERSION, "op": op, **fields}


def error_message(code: str, text: str) -> dict:
    return message("error", error={"code": code, "message": text})


def state_message(op: str, reading: Reading | None) -> dict:
    """A ``state`` reply or a stream ``sample``: the state (seq and age_ms inside it), rx_mono and link."""
    if reading is None:
        return message(op, state=None, rx_mono=None, link="down")
    return message(op, state=asdict(reading.state), rx_mono=reading.rx_mono, link=reading.link)


@dataclass(frozen=True)
class MoveTarget:
    """A ``move``'s stop condition, read from the state each tick: the yaw change in radians, signed (CCW +).
    ``lead_s``: the start-ramp time added when no fresh yaw is readable at the start and the move runs open loop."""

    yaw_rad: float
    lead_s: float


@dataclass(frozen=True)
class MoveResult:
    """A finished ``move``: commanded seconds (Moves sent x period), the failing Move's code (0 if none failed),
    the closing StopMove's code (None if it raised) and, with a target, the measured yaw change at the stop (None
    when the move fell back to open loop)."""

    duration_s: float
    move_ret: int
    stop_ret: int | None
    yaw_rad: float | None = None


@dataclass(frozen=True)
class StopFailure:
    """The lease's latest StopMove failed: why the service sent it, its code (None if it raised) and its time."""

    reason: str
    ret: int | None
    stop_call_ms: float


@dataclass(frozen=True)
class StopSent:
    """The reply to ``stop``: StopMove's code (None if it raised) and the call's time in the service."""

    ret: int | None
    stop_call_ms: float


@dataclass(frozen=True)
class StateReply:
    """A state read through the service; ``state`` is None when the service has had no message yet."""

    state: RobotState | None
    rx_mono: float | None  # arrival of the message in the service, time.monotonic() on this host
    link: Link

    @property
    def seq(self) -> int:
        return 0 if self.state is None or self.state.seq is None else self.state.seq


@dataclass(frozen=True)
class LinkEvent:
    """A link change in a stream. ``rx_mono``: arrival of the latest message when the change was seen (for
    "down" the last message before the gap, for "up" one after it); None if no message ever arrived."""

    link: Link
    rx_mono: float | None
