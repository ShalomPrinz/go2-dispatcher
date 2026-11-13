"""The latest robot state message with its sequence number and arrival time, shared by the robot service and
the utilities' own DDS reads (skills/docs/robot.md). Standard library and contract only."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from contract.constants import Link
from contract.schema import RobotState

STATE_WAIT_S = 1.0  # longest wait for a message newer than the request (skills/docs/robot.md)
LINK_GAP_S = 0.5  # no message for this long -> link "down" (tunable, skills/docs/robot.md)


@dataclass(frozen=True)
class Reading:
    """One state read: the mapped state (with seq, age_ms and t set), its arrival time and the link at the read."""

    state: RobotState
    rx_mono: float  # time.monotonic() when the message arrived (CLOCK_MONOTONIC, same host)
    link: Link


class StateStore:
    """Holds the latest message only. put() is the subscriber callback: it stores the message, numbers it and
    stamps its arrival; mapping to RobotState happens on read, outside the callback. ``clock`` stamps arrivals
    and measures age and link; waits for a message always use real time."""

    def __init__(self, to_state: Callable[[Any], RobotState], clock: Callable[[], float] = time.monotonic) -> None:
        self._to_state = to_state
        self._clock = clock
        self._cond = threading.Condition()
        self._latest: tuple[int, float, Any] | None = None  # (seq, rx_mono, msg)

    def put(self, msg: Any) -> None:
        with self._cond:
            seq = 1 if self._latest is None else self._latest[0] + 1
            self._latest = (seq, self._clock(), msg)
            self._cond.notify_all()

    @property
    def seq(self) -> int:
        """The latest message's seq; 0 before the first message."""
        with self._cond:
            return 0 if self._latest is None else self._latest[0]

    def link(self) -> tuple[Link, float | None]:
        """The link now and the arrival time of the latest message (None before the first)."""
        with self._cond:
            latest = self._latest
        if latest is None:
            return "down", None
        return ("up" if self._clock() - latest[1] <= LINK_GAP_S else "down"), latest[1]

    def read(self, min_seq: int | None = None, wait_s: float = 0.0) -> Reading | None:
        """Wait up to ``wait_s`` for a message with seq >= ``min_seq``, then read the latest message, which is
        older than asked if none came in time; None if no message ever arrived."""
        deadline = time.monotonic() + wait_s
        with self._cond:
            while min_seq is not None and (self._latest is None or self._latest[0] < min_seq):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            latest = self._latest
        if latest is None:
            return None
        seq, rx_mono, msg = latest
        now = self._clock()
        state = dataclasses.replace(
            self._to_state(msg), t=time.time(), seq=seq, age_ms=round((now - rx_mono) * 1000.0, 3)
        )
        return Reading(state=state, rx_mono=rx_mono, link="up" if now - rx_mono <= LINK_GAP_S else "down")

    def read_next(self, wait_s: float) -> Reading | None:
        """The first message that arrives after this call, else the latest earlier one (skills/docs/robot.md)."""
        return self.read(self.seq + 1, wait_s)
