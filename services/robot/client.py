"""The robot service's client (services/robot/docs/service.md): one persistent connection speaking
services/robot/protocol.py. Standard library, contract and services.robot.protocol only; imported by skills (state
samples and motion primitives on one connection per process) and by the dispatcher (control connection)."""

from __future__ import annotations

import os
import time
from dataclasses import asdict
from multiprocessing.connection import Client
from typing import TYPE_CHECKING, TypeGuard

from contract.constants import Link
from contract.env import LEASE_TOKEN, ROBOT_SOCKET
from contract.schema import ObstacleDistances, RobotState
from contract.wire import FAMILY, decode, encode
from services.robot.protocol import (
    ACTION_WAIT_S,
    ERR_ABORTED,
    ERR_BUSY,
    ERR_NO_LEASE,
    ERR_PROTOCOL_VERSION,
    ERR_SDK_EXCEPTION,
    MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
    REPLY_WAIT_S,
    ROLE_CONTROL,
    SPORT_CLIENT_TIMEOUT_S,
    LinkEvent,
    MoveResult,
    MoveTarget,
    StateReply,
    StateUnavailable,
    StopFailure,
    StopSent,
    message,
)
from services.robot.state_store import STATE_WAIT_S

if TYPE_CHECKING:
    from contract.wire import Conn


class ServiceUnavailable(Exception):
    """The robot service could not be reached, did not answer in time, or sent a malformed or closing error."""


class ProtocolMismatch(ServiceUnavailable):
    """The service speaks another protocol version."""


class Refused(Exception):
    """The service refused a request and keeps the connection: ``code`` is no_lease, aborted or sdk_exception."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def state_from_dict(data: dict) -> RobotState:
    """A RobotState from its JSON form; raises TypeError or ValueError if it does not fit the schema."""
    obstacles = data.get("obstacle_m")
    return RobotState(**{**data, "obstacle_m": None if obstacles is None else ObstacleDistances(**obstacles)})


def _state_reply(reply: dict) -> StateReply:
    try:
        state = reply["state"]
        link = reply["link"]
        if link not in ("up", "down"):
            raise ValueError(f"link {link!r}")
        return StateReply(state=None if state is None else state_from_dict(state), rx_mono=reply["rx_mono"], link=link)
    except (KeyError, TypeError, ValueError) as e:
        raise ServiceUnavailable(f"malformed reply: {e}") from None


def socket_path() -> str:
    path = os.environ.get(ROBOT_SOCKET, "")
    if not path:
        raise ServiceUnavailable(f"{ROBOT_SOCKET} is not set")
    return path


class RobotClient:
    """One persistent connection to the robot service. Not thread-safe: one thread per client."""

    def __init__(self, conn: Conn, hello: dict) -> None:
        self._conn = conn
        self.hello = hello  # the service's hello reply: backend, pid

    @classmethod
    def connect(cls, path: str, wait_s: float = REPLY_WAIT_S, *, control: bool = False) -> RobotClient:
        """Connect and exchange ``hello``; raises ServiceUnavailable, or ProtocolMismatch on another version.
        ``control``: open the dispatcher's control connection, refused with Refused(busy) while one is open."""
        try:
            conn = Client(path, family=FAMILY)
        except OSError as e:
            raise ServiceUnavailable(f"no robot service at {path}: {e}") from None
        client = cls(conn, {})
        hello = message("hello", role=ROLE_CONTROL) if control else message("hello")
        try:
            client.hello = client._request(hello, "hello", wait_s)
        except BaseException:
            conn.close()
            raise
        return client

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> RobotClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _send(self, msg: dict) -> None:
        try:
            self._conn.send_bytes(encode(msg))
        except OSError as e:
            raise ServiceUnavailable(f"connection lost: {type(e).__name__}: {e}") from None

    def _receive(self, wait_s: float) -> dict | None:
        """The next message, None if none arrives within ``wait_s``; an error message raises."""
        try:
            if not self._conn.poll(wait_s):
                return None
            data = self._conn.recv_bytes(MAX_MESSAGE_BYTES)
        except (OSError, EOFError) as e:
            raise ServiceUnavailable(f"connection lost: {type(e).__name__}: {e}") from None
        try:
            reply = decode(data)
        except ValueError as e:
            raise ServiceUnavailable(f"malformed reply: {e}") from None
        error = reply.get("error") if reply.get("op") == "error" else None
        if isinstance(error, dict) and error.get("code") == ERR_PROTOCOL_VERSION:
            raise ProtocolMismatch(f"service: {error.get('message')}")
        if reply.get("v") != PROTOCOL_VERSION:
            raise ProtocolMismatch(f"service protocol version {reply.get('v')!r}, client {PROTOCOL_VERSION}")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            if code in (ERR_BUSY, ERR_NO_LEASE, ERR_ABORTED, ERR_SDK_EXCEPTION):
                raise Refused(str(code), str(error.get("message")))
            raise ServiceUnavailable(f"service error: {error}")
        return reply

    def _request(self, msg: dict, op: str, wait_s: float) -> dict:
        self._send(msg)
        reply = self._receive(wait_s)
        if reply is None:
            raise ServiceUnavailable(f"no reply within {wait_s:g}s")
        if reply.get("op") != op:
            raise ServiceUnavailable(f"malformed reply: op {reply.get('op')!r}, expected {op!r}")
        return reply

    def state(self, min_seq: int | None = None, wait_s: float = 0.0) -> StateReply:
        """The latest state; with ``min_seq``, the service first waits up to ``wait_s`` for a message with
        seq >= min_seq and answers with the latest earlier one if none comes."""
        request = message("state", min_seq=min_seq, wait_ms=round(wait_s * 1000.0, 3))
        return _state_reply(self._request(request, "state", wait_s + REPLY_WAIT_S))

    def read_next(self, wait_s: float = STATE_WAIT_S) -> StateReply:
        """The first message after this call, else the latest earlier one: min_seq = the latest seq + 1."""
        return self.state(self.state().seq + 1, wait_s)

    def subscribe(self, period_s: float) -> Link:
        """Turn this connection into a stream of new samples, at most one per ``period_s``, and link changes;
        returns the link at subscription. Read the stream with next_event()."""
        reply = self._request(message("subscribe", period_ms=round(period_s * 1000.0, 3)), "subscribed", REPLY_WAIT_S)
        link = reply.get("link")
        if link not in ("up", "down"):
            raise ServiceUnavailable(f"malformed reply: link {link!r}")
        return link

    def next_event(self, wait_s: float) -> StateReply | LinkEvent | None:
        """The next stream event, or None if none arrives within ``wait_s``."""
        event = self._receive(wait_s)
        if event is None:
            return None
        if event.get("op") == "sample":
            return _state_reply(event)
        if event.get("op") == "link" and event.get("link") in ("up", "down"):
            return LinkEvent(link=event["link"], rx_mono=event.get("rx_mono"))
        raise ServiceUnavailable(f"malformed stream event: {event!r}")

    # --- control connection (the dispatcher) ---------------------------------------------

    def arm(self, token: str, timeout_s: float) -> None:
        """Arm the next step: motion is accepted with ``token`` on one connection until ``timeout_s`` passes."""
        self._request(message("arm", token=token, timeout_s=timeout_s), "armed", REPLY_WAIT_S)

    def disarm(self) -> StopFailure | None:
        """End the lease (StopMove if it was used for motion); the lease's latest StopMove if it failed, else None."""
        reply = self._request(message("disarm"), "disarmed", REPLY_WAIT_S + SPORT_CLIENT_TIMEOUT_S)
        failed = reply.get("stop_failed", {})
        if failed is None:
            return None
        if not isinstance(failed, dict):
            raise ServiceUnavailable(f"malformed reply: {reply!r}")
        reason, ret, call_ms = failed.get("reason"), failed.get("ret"), failed.get("stop_call_ms")
        if not isinstance(reason, str) or not (ret is None or _is_int(ret)) or not isinstance(call_ms, (int, float)):
            raise ServiceUnavailable(f"malformed reply: {reply!r}")
        return StopFailure(reason=reason, ret=ret, stop_call_ms=float(call_ms))

    def stop(self, wait_s: float) -> StopSent:
        """StopMove now through the service's sport client and end any lease; raises ServiceUnavailable if no
        ``stop_sent`` arrives within ``wait_s``."""
        reply = self._request(message("stop"), "stop_sent", wait_s)
        ret, call_ms = reply.get("ret"), reply.get("stop_call_ms")
        if (ret is not None and not _is_int(ret)) or not isinstance(call_ms, (int, float)):
            raise ServiceUnavailable(f"malformed reply: {reply!r}")
        return StopSent(ret=ret, stop_call_ms=float(call_ms))

    def ping(self, wait_s: float = REPLY_WAIT_S) -> None:
        self._request(message("ping"), "pong", wait_s)

    # --- motion primitives (a skill, with the lease token) -------------------------------

    def move(
        self,
        vx: float,
        vy: float,
        vyaw: float,
        duration_s: float,
        *,
        token: str | None,
        target: MoveTarget | None = None,
    ) -> MoveResult:
        """Move at body-frame (vx, vy, vyaw) for ``duration_s``, then StopMove, in the service's motion loop; with
        ``target``, StopMove once it is reached, ``duration_s`` being the cap. Raises Refused (no lease, aborted,
        SDK exception) or ServiceUnavailable."""
        fields = {"target": asdict(target)} if target is not None else {}
        request = message("move", token=token, vx=vx, vy=vy, vyaw=vyaw, duration_s=duration_s, **fields)
        reply = self._request(request, "move", duration_s + SPORT_CLIENT_TIMEOUT_S + REPLY_WAIT_S)
        duration, move_ret, stop_ret = reply.get("duration_s"), reply.get("move_ret"), reply.get("stop_ret")
        measured = reply.get("measured")
        yaw = measured.get("yaw_rad") if isinstance(measured, dict) else None
        if (
            not isinstance(duration, (int, float))
            or not _is_int(move_ret)
            or not (stop_ret is None or _is_int(stop_ret))
            or (target is None) != (measured is None)
            or (
                measured is not None
                and yaw is not None
                and (isinstance(yaw, bool) or not isinstance(yaw, (int, float)))
            )
        ):
            raise ServiceUnavailable(f"malformed reply: {reply!r}")
        return MoveResult(
            duration_s=float(duration),
            move_ret=move_ret,
            stop_ret=stop_ret,
            yaw_rad=None if yaw is None else float(yaw),
        )

    def action(self, name: str, *, token: str | None) -> int:
        """Run one sport client action (``ACTIONS``) in the service; returns its code. Raises as move()."""
        reply = self._request(message("action", token=token, name=name), "action", ACTION_WAIT_S)
        ret = reply.get("ret")
        if not _is_int(ret):
            raise ServiceUnavailable(f"malformed reply: {reply!r}")
        return ret


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def lease_token() -> str | None:
    """The step's lease token from ``GO2_LEASE_TOKEN``; None when unset (the service then refuses motion)."""
    return os.environ.get(LEASE_TOKEN) or None


class SkillSession:
    """The skill process's one connection to the robot service, for state samples and motion: opened at first
    use, opened again after a failure. ``connect_s`` is the time spent connecting (robot_connect_ms)."""

    def __init__(self) -> None:
        self._client: RobotClient | None = None
        self.connect_s: float | None = None

    def client(self) -> RobotClient:
        if self._client is None:
            t = time.monotonic()
            try:
                self._client = RobotClient.connect(socket_path())
            finally:
                self.connect_s = (self.connect_s or 0.0) + time.monotonic() - t
        return self._client

    def drop(self) -> None:
        """Close the connection after a failure; the service ends a lease bound to it."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def setup_timing(self) -> dict[str, float]:
        return {} if self.connect_s is None else {"robot_connect_ms": round(self.connect_s * 1000.0, 3)}

    def read_state(self) -> RobotState:
        """The skills' state sample: read_next(), the first message after the call."""
        try:
            reply = self.client().read_next(STATE_WAIT_S)
        except ServiceUnavailable:
            self.drop()
            raise
        if reply.state is None:
            raise StateUnavailable(f"the robot service has had no state message within {STATE_WAIT_S:g}s")
        return reply.state


session = SkillSession()  # one per skill process
