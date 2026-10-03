"""Shared plumbing for the hooks; stdlib only (.claude/hooks/README.md)."""

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


def git(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True)


@functools.cache
def tree() -> Path:
    """Git top level of the payload's `cwd` (so worktrees check their own tree); exits 0 outside a repo."""
    cwd = payload().get("cwd")
    for where in (cwd if isinstance(cwd, str) and cwd else ".", None):
        try:
            done = git("rev-parse", "--show-toplevel", cwd=where)
        except OSError:
            continue
        if done.returncode == 0 and done.stdout.strip():
            return Path(os.fsdecode(done.stdout.strip()))
    sys.exit(0)


def agent_type() -> str | None:
    """The calling subagent's `agent_type` (`agent` if unnamed); None for the main session (no `agent_id`)."""
    data = payload()
    return str(data.get("agent_type") or "agent") if data.get("agent_id") else None


def written_path() -> Path | None:
    """The Edit/Write/NotebookEdit target, resolved against the payload `cwd` and `realpath`; None if absent."""
    data = payload()
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return None
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    return Path(os.path.realpath(os.path.join(cwd, raw)))


def _common_dir(where: Path) -> str | None:
    done = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=str(where))
    return os.fsdecode(done.stdout.strip()) if done.returncode == 0 else None


def locate(path: Path) -> tuple[Path, str] | None:
    """`path`'s own git top level and its path relative to it, if that tree is this project's checkout or a worktree."""
    where = path.parent
    while not where.is_dir():
        where = where.parent
    top = git("rev-parse", "--show-toplevel", cwd=str(where))
    if top.returncode != 0 or _common_dir(where) != _common_dir(tree()):
        return None
    root = Path(os.fsdecode(top.stdout.strip()))
    try:
        return root, path.relative_to(root).as_posix()
    except ValueError:
        return None


def record(path: Path) -> None:
    """Append `path` to the caller's touched-files record."""
    if "\n" in str(path):
        return
    try:
        with _state("touched").open("a", encoding="utf-8") as f:
            f.write(f"{path}\n")
    except (OSError, ValueError):
        pass


@functools.cache
def _record() -> str:
    try:
        return _state("touched").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def touched(name: str, suffix: str = ".py") -> dict[Path, list[str]]:
    """Existing `suffix` files the caller wrote since hook `name` last passed for it, by their git top level."""
    text = _record()
    try:
        start = int(_state(f"{name}-checked").read_text())
    except (OSError, ValueError):
        start = 0
    files: dict[Path, list[str]] = {}
    for line in dict.fromkeys(text[start if start <= len(text) else 0 :].splitlines()):
        found = locate(Path(line)) if line.endswith(suffix) else None
        if found and (found[0] / found[1]).is_file():
            files.setdefault(found[0], []).append(found[1])
    return files


def run(*cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command in `cwd` (default `tree()`), stdout and stderr merged."""
    return subprocess.run(cmd, cwd=cwd or tree(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def report(msg: str) -> None:
    """Print the hook's one JSON object; `systemMessage` is the only line the user sees."""
    print(json.dumps({"systemMessage": msg, "suppressOutput": True}, ensure_ascii=False))


def _clean(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))


def _state(name: str) -> Path:
    """Per-caller state file: `claude-<name>-<session_id>-<agent_id or main>` in `$TMPDIR`."""
    data = payload()
    caller = f"{_clean(data.get('session_id')) or 'unknown'}-{_clean(data.get('agent_id')) or 'main'}"
    return Path(os.environ.get("TMPDIR") or "/tmp") / f"claude-{name}-{caller}"


def passed(name: str) -> None:
    """After a clean run of hook `name`: forget its last block and mark the caller's record checked up to here."""
    _state(name).unlink(missing_ok=True)
    try:
        _state(f"{name}-checked").write_text(f"{len(_record())}\n")
    except OSError:
        pass


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
