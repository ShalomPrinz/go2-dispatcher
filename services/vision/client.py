"""The vision service's client (services/vision/docs/vision.md): the socket path, the connection and one detect request.
Standard library, contract and services.vision.protocol only; imported by detect_object."""

from __future__ import annotations

import os
from multiprocessing.connection import Client
from typing import TYPE_CHECKING

from contract.env import VISION_SOCKET
from contract.schema import describe_exception
from contract.wire import FAMILY, decode, encode
from services.vision.protocol import MAX_MESSAGE_BYTES, DetectResult, VisionUnavailable, parse_reply

if TYPE_CHECKING:
    from contract.wire import Conn


def socket_path() -> str:
    path = os.environ.get(VISION_SOCKET, "")
    if not path:
        raise VisionUnavailable(f"{VISION_SOCKET} is not set")
    return path


def connect(path: str) -> Conn:
    """Connect to the service; raises VisionUnavailable if nothing listens at ``path``."""
    try:
        return Client(path, family=FAMILY)
    except OSError as e:
        raise VisionUnavailable(f"no vision service at {path}: {e}") from None


def detect(conn: Conn, target: str, wait_s: float) -> DetectResult:
    """Send ``{"target": target}`` and wait up to ``wait_s`` for the reply."""
    try:
        conn.send_bytes(encode({"target": target}))
        if not conn.poll(wait_s):
            raise VisionUnavailable(f"no reply within {wait_s:g}s")
        data = conn.recv_bytes(MAX_MESSAGE_BYTES)
    except (OSError, EOFError) as e:
        raise VisionUnavailable(f"connection lost: {describe_exception(e)}") from None
    try:
        reply = decode(data)
    except ValueError as e:
        raise VisionUnavailable(f"malformed reply: {e}") from None
    return parse_reply(reply)
