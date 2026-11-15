"""The utilities' own robot access, without the robot service (services/robot/docs/service.md): DDS and a fresh sport
client on the real backend, the stub state file and SimRobot on the stub. Used by stop_move (the fallback stop)
and read_state (the diagnosis read) only; skills reach the robot through the robot service."""

from __future__ import annotations

from typing import Any

from contract import backend
from contract.schema import RobotState
from services.backend import dds
from services.robot.backends import sim

_stub_dds_init = False  # the stub has no DDS; an init is recorded so dds_init_ms is reported as on the robot


def init_dds() -> None:
    """DDS init once per process; its time is reported as dds_init_ms. Raises BackendNotConfigured."""
    global _stub_dds_init
    if backend.backend_name() == "real":
        dds.init_dds()
    else:
        _stub_dds_init = True


def setup_timing() -> dict[str, float]:
    """``{"dds_init_ms": ...}`` if this process attempted DDS init, else empty."""
    try:
        name = backend.backend_name()
    except backend.BackendNotConfigured:
        return {}
    if name == "real":
        ms = dds.dds_init_ms()
    else:
        ms = 0.0 if _stub_dds_init else None
    return {} if ms is None else {"dds_init_ms": ms}


def sport_client() -> Any:
    """A sport client in this process (call init_dds first): object with StopMove() -> int."""
    if backend.backend_name() == "real":
        from services.robot.backends import real

        return real.get_sport_client()
    return sim.SimRobot()


def sample_state() -> RobotState:
    """One state sample in this process: real: its own rt/sportmodestate subscription; stub: the state file."""
    if backend.backend_name() == "real":
        from services.robot.backends import real

        return real.sample_state()
    return sim.sample_state()
