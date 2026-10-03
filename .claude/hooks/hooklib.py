"""Shared plumbing for the Stop/SubagentStop hooks; stdlib only (.claude/hooks/README.md)."""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn


@functools.cache
def payload() -> dict[str, Any]:
    """The hook's stdin JSON, parsed once; `{}` on bad input."""
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def stop_hook_active() -> bool:
    """True once the agent already continued because of a Stop hook; the hook must then exit 0 at once."""
    return payload().get("stop_hook_active") is True


def _git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


@functools.cache
def tree() -> Path:
    """Git top level of the payload's `cwd` (so worktrees check their own tree); exits 0 outside a repo."""
    cwd = payload().get("cwd")
    for where in (cwd if isinstance(cwd, str) and cwd else ".", None):
        try:
            done = _git("rev-parse", "--show-toplevel", cwd=where)
        except OSError:
            continue
        if done.returncode == 0 and done.stdout.strip():
            return Path(os.fsdecode(done.stdout.strip()))
    sys.exit(0)


def changed_files(suffix: str = ".py") -> list[str]:
    """Changed (vs HEAD) and untracked files with `suffix` that exist on disk, relative to `tree()`."""
    root = tree()
    names: list[str] = []
    for args in (("diff", "-z", "--name-only", "HEAD"), ("ls-files", "-z", "--others", "--exclude-standard")):
        names += [os.fsdecode(n) for n in _git(*args, cwd=str(root)).stdout.split(b"\0") if n]
    return [n for n in dict.fromkeys(names) if n.endswith(suffix) and (root / n).is_file()]


def run(*cmd: str) -> subprocess.CompletedProcess[str]:
    """Run a command in `tree()`, stdout and stderr merged."""
    return subprocess.run(cmd, cwd=tree(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def report(msg: str) -> None:
    """Print the hook's one JSON object; `systemMessage` is the only line the user sees."""
    print(json.dumps({"systemMessage": msg, "suppressOutput": True}, ensure_ascii=False))


def _state(name: str) -> Path:
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(payload().get("session_id") or "")) or "unknown"
    return Path(os.environ.get("TMPDIR") or "/tmp") / f"claude-{name}-{session}"


def passed(name: str) -> None:
    """Forget the last block of hook `name` after a clean run."""
    _state(name).unlink(missing_ok=True)


def block(name: str, output: str, *, reason: str, command: str, done: Sequence[str] = ()) -> NoReturn:
    """Block the stop with `reason` and `output` (exit 2), unless the same output already blocked once.

    A repeat reports `<name> ✗ unchanged ...` after the `done` status parts and exits 0 (loop breaker).
    """
    state = _state(name)
    digest = hashlib.md5(output.encode()).hexdigest()
    try:
        last = state.read_text().strip()
    except OSError:
        last = ""
    if last == digest:
        report(" · ".join([*done, f"{name} ✗ unchanged since the last block; not blocking again (run {command})"]))
        sys.exit(0)
    try:
        state.write_text(digest + "\n")
    except OSError:
        pass
    print(reason, file=sys.stderr)
    print(output.rstrip("\n"), file=sys.stderr)
    sys.exit(2)
