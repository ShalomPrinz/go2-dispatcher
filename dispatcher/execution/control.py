"""The dispatcher's one control connection to the robot service and its lock (services/robot/docs/service.md,
docs/safety.md)."""

from __future__ import annotations

import threading

from services.robot.client import Refused, RobotClient, ServiceUnavailable
from services.robot.protocol import StopFailure, StopSent

from ..models import RobotServiceError
from .robot_service import RobotSupervisor

STOP_WAIT_S = 0.5  # wait for stop_sent or pong before the service counts as lost (tunable, docs/safety.md)
LIVENESS_PERIOD_S = 0.5  # ping period on the control connection while a step runs (tunable, docs/safety.md)

_SERVICE_ERRORS = (RobotServiceError, ServiceUnavailable, Refused)  # the control connection cannot be used


def error_text(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


class ServiceLost(Exception):
    """The control connection cannot be used: the service is not running, not reachable, or did not answer."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "{ExceptionType}: {text}", the stop record's service_error


class ControlLink:
    """One user of the control connection at a time; it is opened on first use and never starts the service.
    Every call but ``open`` and ``close`` raises ``ServiceLost``."""

    def __init__(self, robot: RobotSupervisor):
        self._robot = robot
        self._lock = threading.Lock()
        self._client: RobotClient | None = None

    def _connection(self) -> RobotClient:
        """The connection, opened if needed (caller holds the lock). Raises RobotServiceError, ServiceUnavailable
        or Refused."""
        if self._client is None:
            if self._robot.pid is None:
                raise RobotServiceError("Robot service not running.")
            self._client = RobotClient.connect(self._robot.socket_path, control=True)
        return self._client

    def open(self) -> None:
        """Open the connection at start-up; raises RobotServiceError (a start-up error, docs/running.md)."""
        try:
            with self._lock:
                self._connection()
        except (ServiceUnavailable, Refused) as e:
            raise RobotServiceError(f"Robot service control connection failed: {error_text(e)}") from None

    def arm(self, token: str, timeout_s: float) -> None:
        try:
            with self._lock:
                self._connection().arm(token, timeout_s)
        except _SERVICE_ERRORS as e:
            raise ServiceLost(error_text(e)) from None

    def disarm(self) -> StopFailure | None:
        """End the step's lease; the lease's latest StopMove if the service reports it failed, else None."""
        try:
            with self._lock:
                return self._connection().disarm()
        except _SERVICE_ERRORS as e:
            raise ServiceLost(error_text(e)) from None

    def ping(self) -> None:
        """Returns once the service answers within STOP_WAIT_S."""
        try:
            with self._lock:
                self._connection().ping(STOP_WAIT_S)
        except _SERVICE_ERRORS as e:
            raise ServiceLost(error_text(e)) from None

    def stop(self) -> StopSent:
        """The service's ``stop``: StopMove and end any lease; ServiceLost if no ``stop_sent`` within STOP_WAIT_S."""
        try:
            with self._lock:
                return self._connection().stop(STOP_WAIT_S)
        except _SERVICE_ERRORS as e:
            raise ServiceLost(error_text(e)) from None

    def close(self) -> None:
        """Close the connection, if open; the service sends StopMove when it closes. The next use opens a new one."""
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()
