"""Where events go: the task's log and its observers (dispatcher/docs/run-log.md)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .events import Event, TaskEvent


class TaskSink(Protocol):
    """Writes task events: ``RunLog``."""

    def emit(self, event: TaskEvent) -> None: ...


class Observer(Protocol):
    """Presents events, e.g. the CLI console trace; matches the ones it shows and ignores the rest. Task events
    come from the loop or the transport thread, stream events from the stream thread (dispatcher/docs/run-log.md)."""

    def emit(self, event: Event) -> None: ...


def publish(event: TaskEvent, log: TaskSink | None, observers: Sequence[Observer]) -> None:
    """Write ``event`` to ``log``, if any, then hand it to each observer. A log error propagates (and no observer
    sees the event); an observer error is swallowed, so it never breaks a task or the log."""
    if log is not None:
        log.emit(event)
    for observer in observers:
        try:
            observer.emit(event)
        except Exception:  # noqa: BLE001 - an observer never breaks a task
            pass
