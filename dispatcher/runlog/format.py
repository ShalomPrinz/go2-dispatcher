"""Run-log file format: file names, envelope keys and index token columns (dispatcher/docs/run-log.md)."""

from __future__ import annotations

from pathlib import Path

from ..config import Provider

INDEX_FILE = "index.jsonl"
STREAM_SUFFIX = ".state_stream.jsonl"  # beside the task's log file (dispatcher/docs/run-log.md)
ROBOT_IO = "service"  # how robot state is read; set by the code, never by config (dispatcher/docs/run-log.md)
FILE_TIME_FORMAT = "%Y%m%dT%H%M%S"  # local time at task start (dispatcher/docs/run-log.md)
RUN_ID_PREFIX_LEN = 8
ENVELOPE_KEYS = frozenset({"ts", "t_mono_ms", "session_id", "run_id", "seq", "type"})
# usage keys summed into the index token columns, per provider (dispatcher/docs/run-log.md)
TOKEN_KEYS: dict[Provider, tuple[str, str]] = {
    "anthropic": ("input_tokens", "output_tokens"),
    "openrouter": ("prompt_tokens", "completion_tokens"),
}


def stream_path(log_path: Path) -> Path:
    """The task's ``state_stream.jsonl``: its log file's name with ``STREAM_SUFFIX``."""
    return log_path.with_name(log_path.stem + STREAM_SUFFIX)
