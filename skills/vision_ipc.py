"""The vision service protocol and its client (skills/docs/skills.md): one JSON request and one JSON reply
per connection, as bytes over multiprocessing.connection on a Unix socket (no pickle). Standard library
and skills only; imported by detect_object and by the service."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from multiprocessing.connection import Client
from typing import TYPE_CHECKING

from skills import runner
from skills.backend import BackendNotConfigured, DetectorError, DetectResult, VisionUnavailable
from skills.env import VISION_SOCKET
from skills.schema import ErrorCode

if TYPE_CHECKING:  # what Client() and Listener.accept() return: Connection, or PipeConnection on Windows
    from multiprocessing.connection import _ConnectionBase as Conn

FAMILY = "AF_UNIX"
MAX_MESSAGE_BYTES = 4096


class ServiceError(DetectorError):
    """An error the service replied with; str() is its message, already ``<ExceptionType>: <message>``."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def error_message(e: DetectorError) -> str:
    """The skill's error message for a detector failure: ``<ExceptionType>: <message>``."""
    return str(e) if isinstance(e, ServiceError) else f"{type(e).__name__}: {e}"


def encode(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode(data: bytes) -> dict:
    """A JSON object; raises ValueError for anything else."""
    message = json.loads(data.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    return message


# --- service side ---------------------------------------------------------------


def ok_reply(result: DetectResult) -> dict:
    return asdict(result)


def error_reply(e: BaseException) -> dict:
    if isinstance(e, DetectorError):
        code = e.code
    elif isinstance(e, BackendNotConfigured):
        code = ErrorCode.BACKEND_NOT_CONFIGURED
    else:
        code = ErrorCode.DETECTOR_ERROR
    return {"error": {"code": str(code), "message": runner.describe_exception(e)}}


# --- client side ----------------------------------------------------------------


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


def parse_reply(reply: dict) -> DetectResult:
    """The result, or ServiceError for an error reply; VisionUnavailable if the reply is malformed."""
    try:
        if "error" in reply:
            error = reply["error"]
            raise ServiceError(ErrorCode(error["code"]), str(error["message"]))
        result = DetectResult(**reply)
        if result.found and not (
            isinstance(result.position, str)
            and isinstance(result.closeness, str)
            and isinstance(result.confidence, (int, float))
        ):
            raise ValueError("a found result needs position, closeness and confidence")
        return result
    except (KeyError, TypeError, ValueError) as e:
        raise VisionUnavailable(f"malformed reply: {e}") from None


def detect(conn: Conn, target: str, wait_s: float) -> DetectResult:
    """Send ``{"target": target}`` and wait up to ``wait_s`` for the reply."""
    try:
        conn.send_bytes(encode({"target": target}))
        if not conn.poll(wait_s):
            raise VisionUnavailable(f"no reply within {wait_s:g}s")
        data = conn.recv_bytes(MAX_MESSAGE_BYTES)
    except (OSError, EOFError) as e:
        raise VisionUnavailable(f"connection lost: {runner.describe_exception(e)}") from None
    try:
        reply = decode(data)
    except ValueError as e:
        raise VisionUnavailable(f"malformed reply: {e}") from None
    return parse_reply(reply)
