"""The vision service protocol (services/vision/docs/vision.md): one JSON request and one JSON reply per
connection, encoded as contract/wire.py does, the detector's errors and result, and the service's lifecycle
constants. Standard library and contract only; imported by the service, by its client
(services/vision/client.py), by detect_object and by the dispatcher."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from contract.backend import BackendNotConfigured
from contract.schema import ErrorCode, describe_exception

SERVICE_MODULE = "services.vision.service"  # the ``python -m`` target; the dispatcher starts it without importing it
READY_LINE = "ready"
STARTUP_TIMEOUT_S = 30.0  # torch import + YOLO load + first predict + DDS init on the lab machine (tunable)
STOP_GRACE_S = 2.0  # SIGTERM to SIGKILL (tunable)

MAX_MESSAGE_BYTES = 4096


class DetectorError(Exception):
    """Base for detector failures; ``code`` is the skill error code (contract/docs/contract.md)."""

    code = ErrorCode.DETECTOR_ERROR


class CameraUnavailable(DetectorError):
    code = ErrorCode.CAMERA_UNAVAILABLE


class BadFrame(DetectorError):
    code = ErrorCode.BAD_FRAME


class WeightsMissing(DetectorError):
    code = ErrorCode.WEIGHTS_MISSING


class VisionUnavailable(DetectorError):
    """The vision service could not be reached or did not answer (services/vision/docs/vision.md)."""

    code = ErrorCode.VISION_UNAVAILABLE


@dataclass
class DetectResult:
    found: bool
    position: str | None = None
    closeness: str | None = None
    confidence: float | None = None


class ServiceError(DetectorError):
    """An error the service replied with; str() is its message, already ``<ExceptionType>: <message>``."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def error_message(e: DetectorError) -> str:
    """The skill's error message for a detector failure: ``<ExceptionType>: <message>``."""
    return str(e) if isinstance(e, ServiceError) else f"{type(e).__name__}: {e}"


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
    return {"error": {"code": str(code), "message": describe_exception(e)}}


# --- client side ----------------------------------------------------------------


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
