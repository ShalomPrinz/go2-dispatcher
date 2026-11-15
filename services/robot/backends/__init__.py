"""The robot backend interface and its loader (services/robot/docs/service.md#robot-backends): the real-or-sim choice
is made once per process by load(); the robot service and the utilities use only the object it returns."""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable
from typing import Any, Protocol

from contract import backend
from contract.schema import RobotState
from services.robot.state_store import StateStore


class SportClient(Protocol):
    """The calls the service makes; each returns an int, 0 = success (services/robot/docs/robot.md)."""

    def Move(self, vx: float, vy: float, vyaw: float) -> int: ...
    def StopMove(self) -> int: ...
    def StandDown(self) -> int: ...
    def Stretch(self) -> int: ...


Wait = Callable[[float, threading.Event], Any]  # the motion clock: wait seconds or until the event is set


class RobotBackend(Protocol):
    """One robot backend: the utilities' own access (init_dds, setup_timing, sport_client, sample_state) and the
    robot service's (start_source, motion)."""

    name: str  # the GO2_BACKEND value, reported in the service's hello

    def init_dds(self) -> None:
        """DDS init once per process, reported as dds_init_ms. Raises BackendNotConfigured."""

    def setup_timing(self) -> dict[str, float]:
        """``{"dds_init_ms": ...}`` if this process attempted DDS init, else empty."""
        ...

    def sport_client(self) -> SportClient:
        """A fresh sport client in this process, for a utility (call init_dds first)."""
        ...

    def sample_state(self) -> RobotState:
        """One state sample in this process, without the robot service."""
        ...

    def start_source(self) -> tuple[StateStore, object]:
        """The service's store fed by the backend's state messages, and what must stay referenced to keep them
        coming."""
        ...

    def motion(self, store: StateStore) -> tuple[SportClient, Wait]:
        """The service's one sport client and its motion clock."""
        ...

    def sleep(self, seconds: float) -> None:
        """A utility's settle wait: real time on the robot, scaled on the stub."""


def _real() -> RobotBackend:
    from services.robot.backends.real import RealBackend

    return RealBackend()


def _sim() -> RobotBackend:
    from services.robot.backends.sim import SimBackend

    return SimBackend()


BACKENDS: dict[str, Callable[[], RobotBackend]] = {"real": _real, "stub": _sim}


@functools.cache
def _instance(factory: Callable[[], RobotBackend]) -> RobotBackend:
    return factory()


def load() -> RobotBackend:
    """The configured backend, one instance per process; raises BackendNotConfigured."""
    return _instance(backend.select(BACKENDS))


def setup_timing() -> dict[str, float]:
    """A utility's setup timing: the backend's, or empty if the backend is not configured."""
    try:
        return load().setup_timing()
    except backend.BackendNotConfigured:
        return {}
