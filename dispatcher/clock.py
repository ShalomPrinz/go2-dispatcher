"""Clock abstraction (docs/testing.md)."""

from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:  # seconds, monotonic
        ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()
