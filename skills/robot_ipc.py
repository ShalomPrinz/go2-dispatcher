"""The robot service protocol and its client (skills/docs/skills.md): JSON messages on persistent connections,
encoded as skills/ipc.py does. Standard library and skills only; imported by the service, by skills through
runner.StateSampler, and by the dispatcher."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from multiprocessing.connection import Client
from typing import TYPE_CHECKING, Any

from skills.backend import StateUnavailable
from skills.env import ROBOT_SOCKET
from skills.ipc import FAMILY, decode, encode
from skills.schema import ObstacleDistances, RobotState
from skills.state_store import STATE_WAIT_S, Link, Reading

if TYPE_CHECKING:
    from skills.ipc import Conn

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 16384
REPLY_WAIT_S = 2.0  # reply wait for hello and subscribe, and added to a state read's own wait (tunable)
MAX_STATE_WAIT_S = 5.0  # the service caps a state read's wait_ms at this
MIN_STREAM_PERIOD_S = 0.02  # the service raises a shorter subscribe period_ms to this

ERR_PROTOCOL_VERSION = "protocol_version"
ERR_BAD_REQUEST = "bad_request"


class ServiceUnavailable(Exception):
    """The robot service could not be reached, did not answer in time, or sent a malformed or error reply."""


class ProtocolMismatch(ServiceUnavailable):
    """The service speaks another protocol version."""


def message(op: str, **fields: Any) -> dict:
    return {"v": PROTOCOL_VERSION, "op": op, **fields}


def error_message(code: str, text: str) -> dict:
    return message("error", error={"code": code, "message": text})


def state_message(op: str, reading: Reading | None) -> dict:
    """A ``state`` reply or a stream ``sample``: the state (seq and age_ms inside it), rx_mono and link."""
    if reading is None:
        return message(op, state=None, rx_mono=None, link="down")
    return message(op, state=asdict(reading.state), rx_mono=reading.rx_mono, link=reading.link)


# --- client side ----------------------------------------------------------------


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
    def connect(cls, path: str, wait_s: float = REPLY_WAIT_S) -> RobotClient:
        """Connect and exchange ``hello``; raises ServiceUnavailable, or ProtocolMismatch on another version."""
        try:
            conn = Client(path, family=FAMILY)
        except OSError as e:
            raise ServiceUnavailable(f"no robot service at {path}: {e}") from None
        client = cls(conn, {})
        try:
            client.hello = client._request(message("hello"), "hello", wait_s)
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


class ServiceStateReader:
    """The skills' state source for runner.StateSampler: read_next() through the service, on one connection
    per skill process, opened at the first sample and opened again after a failure."""

    def __init__(self) -> None:
        self._client: RobotClient | None = None

    def __call__(self) -> RobotState:
        try:
            if self._client is None:
                self._client = RobotClient.connect(socket_path())
            reply = self._client.read_next(STATE_WAIT_S)
        except ServiceUnavailable:
            if self._client is not None:
                self._client.close()
                self._client = None
            raise
        if reply.state is None:
            raise StateUnavailable(f"the robot service has had no state message within {STATE_WAIT_S:g}s")
        return reply.state
