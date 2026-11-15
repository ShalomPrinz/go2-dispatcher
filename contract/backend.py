"""Backend selection and the backend's clock, shared by the skill runtime and both services
(contract/docs/contract.md). Reads env ``GO2_BACKEND`` (``real`` | ``stub``). Standard library only."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import TypeVar

from contract.env import BACKEND, STUB_TIME_SCALE

BACKENDS = ("real", "stub")
T = TypeVar("T")
DEFAULT_TIME_SCALE = 0.1  # used when the env is unset (manual runs) and as the dispatcher's stub config default


class BackendNotConfigured(Exception):
    """GO2_BACKEND missing or unknown; mapped to code ``backend_not_configured``."""


def backend_name() -> str:
    """The configured backend name; raises BackendNotConfigured."""
    name = os.environ.get(BACKEND, "")
    if name not in BACKENDS:
        if not name:
            raise BackendNotConfigured(f"{BACKEND} is not set (expected real or stub)")
        raise BackendNotConfigured(f"{BACKEND}={name!r} is not one of real, stub")
    return name


def check() -> None:
    """Validate the configured backend; raises BackendNotConfigured."""
    backend_name()


def select(table: Mapping[str, T]) -> T:
    """The ``table`` entry for the configured backend; raises BackendNotConfigured if unset or absent."""
    name = backend_name()
    if name not in table:
        raise BackendNotConfigured(f"{BACKEND}={name!r} is not one of {', '.join(table)}")
    return table[name]


def time_scale() -> float:
    raw = os.environ.get(STUB_TIME_SCALE)
    return float(raw) if raw else DEFAULT_TIME_SCALE


def stub_sleep(seconds: float) -> None:
    """Wait ``seconds`` scaled by the time scale."""
    time.sleep(seconds * time_scale())


def sleep(seconds: float) -> None:
    """A skill's wait: real time on the robot, scaled by the stub time scale on the stub."""
    select({"real": time.sleep, "stub": stub_sleep})(seconds)
