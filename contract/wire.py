"""What the vision and robot service protocols share (contract/docs/contract.md): JSON objects as UTF-8 bytes over
multiprocessing.connection on a Unix socket, never pickle. Standard library only."""

from __future__ import annotations

import json
from multiprocessing.connection import _ConnectionBase as Conn  # what Client() and Listener.accept() return

__all__ = ["FAMILY", "Conn", "decode", "encode"]

FAMILY = "AF_UNIX"


def encode(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(data: bytes) -> dict:
    """A JSON object; raises ValueError for anything else."""
    message = json.loads(data.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    return message
