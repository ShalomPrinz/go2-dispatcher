"""A test-controlled monotonic clock for the clock seams of the robot and skills packages (tests/docs/testing.md)."""

from __future__ import annotations


class Clock:
    """A monotonic clock that moves only when the test advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
