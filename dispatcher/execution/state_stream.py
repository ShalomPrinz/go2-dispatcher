"""The per-task state stream from the robot service (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import dataclasses
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from services.robot.client import RobotClient
from services.robot.protocol import LinkEvent

if TYPE_CHECKING:
    from ..runlog import TaskObserver

STREAM_PERIOD_S = 0.1  # state_stream.jsonl at 10 Hz (tunable, dispatcher/docs/run-log.md)
STREAM_POLL_S = 0.2  # the stream thread's wait per event, so close() ends it within this
STREAM_JOIN_S = 1.0  # close() waits at most this for the stream thread; it never blocks a task longer
OBSERVER_FAILED_WARNING = "Warning: state stream observer failed: {exception_type}."


class StateStream:
    """Writes the service's state stream for one task to ``path`` on a daemon thread, until ``close``, and hands
    each sample and link change to ``observer``.

    Every failure ends the stream with a ``stream_error`` line and nothing else: the stream never fails or
    blocks a task. An observer error is printed to stderr once and never ends the stream. ``t_mono_ms`` is
    relative to ``t_start`` (time.monotonic(), the task log's clock)."""

    def __init__(
        self,
        socket_path: str,
        path: Path,
        t_start: float,
        observer: TaskObserver | None = None,
        period_s: float = STREAM_PERIOD_S,
    ):
        self._socket_path = socket_path
        self._path = Path(path)
        self._t_start = t_start
        self._observer = observer
        self._observer_failed = False
        self._period_s = period_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="state-stream", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            pass  # no thread, no stream; the task runs without it

    def close(self) -> None:
        self._stop.set()
        if self._thread.ident is not None:
            self._thread.join(STREAM_JOIN_S)

    def _ms(self, mono: float | None) -> float | None:
        return None if mono is None else (mono - self._t_start) * 1000.0

    def _run(self) -> None:
        try:
            f = self._path.open("a", encoding="utf-8")
        except OSError:
            return  # no file, nowhere to report; the task log is unaffected
        with f:

            def write(type: str, **fields: Any) -> None:
                f.write(json.dumps({"type": type, **fields}, ensure_ascii=False, default=str) + "\n")
                f.flush()

            try:
                self._stream(write)
            except Exception as e:  # noqa: BLE001 - the stream never fails a task (dispatcher/docs/run-log.md)
                try:
                    write("stream_error", t_mono_ms=self._ms(time.monotonic()), detail=f"{type(e).__name__}: {e}")
                except (OSError, ValueError):
                    pass

    def _tell(self, record: Callable[[TaskObserver], None]) -> None:
        """Hand one event to the observer; its errors are printed once per stream, never raised."""
        if self._observer is None:
            return
        try:
            record(self._observer)
        except Exception as e:  # noqa: BLE001 - telemetry never ends the stream (dispatcher/docs/run-log.md)
            if not self._observer_failed:
                self._observer_failed = True
                print(OBSERVER_FAILED_WARNING.format(exception_type=type(e).__name__), file=sys.stderr, flush=True)

    def _stream(self, write: Callable[..., None]) -> None:
        with RobotClient.connect(self._socket_path) as client:
            link = client.subscribe(self._period_s)
            write(
                "stream_start",
                t_mono_ms=self._ms(time.monotonic()),
                period_ms=self._period_s * 1000.0,
                link=link,
            )
            self._tell(lambda o: o.robot_link(link, None))
            while not self._stop.is_set():
                event = client.next_event(STREAM_POLL_S)
                if event is None:
                    continue
                if isinstance(event, LinkEvent):
                    write("link", t_mono_ms=self._ms(event.rx_mono), link=event.link)
                    self._tell(lambda o, e=event: o.robot_link(e.link, e.rx_mono))
                elif event.state is not None:
                    state = dataclasses.asdict(event.state)
                    write("sample", t_mono_ms=self._ms(event.rx_mono), link=event.link, state=state)
                    self._tell(lambda o, e=event, s=event.state: o.robot_sample(s, e.link, e.rx_mono))
