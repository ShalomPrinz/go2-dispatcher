"""A test-controlled monotonic clock for the clock seams in the skills package (skills/docs/skills.md)."""

from __future__ import annotations


class Clock:
    """A monotonic clock that moves only when the test advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
