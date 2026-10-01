"""JSONL run log: one file per task plus an index (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from .clock import Clock, MonotonicClock

INDEX_FILE = "index.jsonl"
FILE_TIME_FORMAT = "%Y%m%dT%H%M%S"     # local time at task start (dispatcher/docs/run-log.md)
RUN_ID_PREFIX_LEN = 8
ENVELOPE_KEYS = frozenset({"ts", "t_mono_ms", "session_id", "run_id", "seq", "type"})


def _local_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class RunLog:
    """One task's log file. ``write`` is thread-safe (``stop_requested`` comes from the
    transport thread) and flushes every line, so a crash leaves a usable partial log."""

    def __init__(self, path: Path, *, session_id: str, run_id: str, t_start_mono: float,
                 clock: Clock):
        self.path = Path(path)
        self._session_id = session_id
        self._run_id = run_id
        self._t0 = t_start_mono
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0
        self._f: TextIO | None = self.path.open("a", encoding="utf-8")

    def write(self, type: str, /, **payload: Any) -> None:
        with self._lock:
            if self._f is None:
                return
            record = {
                "ts": _local_iso(),
                "t_mono_ms": (self._clock.now() - self._t0) * 1000.0,
                "session_id": self._session_id,
                "run_id": self._run_id,
                "seq": self._seq,
                "type": type,
            }
            clash = ENVELOPE_KEYS.intersection(payload)
            if clash:
                raise ValueError(f"payload keys clash with the record envelope: {sorted(clash)}")
            record.update(payload)
            self._f.write(_dumps(record) + "\n")
            self._f.flush()
            self._seq += 1

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


class RunLogFactory:
    def __init__(self, log_dir: Path, session_id: str):
        self.log_dir = Path(log_dir)
        self.session_id = session_id
        self._index_lock = threading.Lock()

    def open(self, run_id: str, t_start_mono: float, *, clock: Clock | None = None) -> RunLog:
        """``clock`` is the clock ``t_start_mono`` was read from (default: monotonic)."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now().strftime(FILE_TIME_FORMAT)}_{run_id[:RUN_ID_PREFIX_LEN]}.jsonl"
        return RunLog(self.log_dir / name, session_id=self.session_id, run_id=run_id,
                      t_start_mono=t_start_mono, clock=clock or MonotonicClock())

    def append_index(self, row: dict) -> None:
        with self._index_lock:
            with (self.log_dir / INDEX_FILE).open("a", encoding="utf-8") as f:
                f.write(_dumps(row) + "\n")
                f.flush()
