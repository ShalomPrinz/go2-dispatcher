"""One skill or utility process: spawn in its own process group, pipe readers, kill, response parsing
(contract/docs/contract.md, dispatcher/docs/execution.md)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from services.robot.protocol import Utility

from ..models import ProcessRun, SkillResponse

READER_JOIN_TIMEOUT_S = 2.0  # join timeout per reader thread (docs/safety.md)
STDERR_TAIL_CHARS = 2000  # stderr kept for the log (contract/docs/contract.md)

_RESPONSE_ADAPTER = TypeAdapter(SkillResponse)  # built once; validates the stdlib dataclass


def stderr_tail(text: str | None) -> str | None:
    """The last STDERR_TAIL_CHARS characters of ``text``, or None when empty."""
    return (text or "")[-STDERR_TAIL_CHARS:] or None


def parse_response(stdout: str, skill_name: str) -> SkillResponse | None:
    """Last non-empty stdout line as a SkillResponse for ``skill_name``, else None."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        resp = _RESPONSE_ADAPTER.validate_json(lines[-1])
    except ValidationError:
        return None
    return resp if resp.skill == skill_name else None


def spawn(module: str, params: dict, env: dict[str, str], cwd: Path) -> subprocess.Popen:
    """``python -m module '<params>'`` in a new session, stdout and stderr piped; raises OSError."""
    argv = [sys.executable, "-m", module, json.dumps(params)]
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        start_new_session=True,
    )


def killpg(pid: int) -> None:
    """SIGKILL the process group ``pid`` leads; a group already gone is fine."""
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _Readers:
    """Two daemon threads draining stdout (kept whole) and stderr (tail only). Each thread closes its pipe at EOF."""

    def __init__(self, proc: subprocess.Popen):
        self._stdout: list[str] = []
        self._stderr = ""
        self._threads = [
            threading.Thread(target=self._read_stdout, args=(proc.stdout,), daemon=True),
            threading.Thread(target=self._read_stderr, args=(proc.stderr,), daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _read_stdout(self, stream) -> None:
        try:
            with stream:  # the reading thread owns the pipe, so a close never waits on a blocked read
                for chunk in iter(lambda: stream.read(4096), ""):
                    self._stdout.append(chunk)
        except (OSError, ValueError):
            pass

    def _read_stderr(self, stream) -> None:
        try:
            with stream:
                for chunk in iter(lambda: stream.read(4096), ""):
                    self._stderr = stderr_tail(self._stderr + chunk) or ""
        except (OSError, ValueError):
            pass

    def join(self) -> None:
        for t in self._threads:
            t.join(READER_JOIN_TIMEOUT_S)

    @property
    def stdout(self) -> str:
        return "".join(self._stdout)

    @property
    def stderr_tail(self) -> str | None:
        return stderr_tail(self._stderr)


class SkillProcess:
    """One running skill process group with its pipe readers; ``finish`` once it has exited."""

    def __init__(self, module: str, params: dict, env: dict[str, str], cwd: Path):
        self._proc = spawn(module, params, env, cwd)
        self._readers = _Readers(self._proc)

    @property
    def pid(self) -> int:
        return self._proc.pid

    def poll(self) -> int | None:
        return self._proc.poll()

    def kill(self) -> None:
        """SIGKILL the whole process group; the caller checks it has not exited first (KillSwitch)."""
        killpg(self._proc.pid)

    def join(self) -> None:
        """Wait for the pipe readers, at most READER_JOIN_TIMEOUT_S each; call after ``poll`` returned the exit code."""
        self._readers.join()

    def finish(self, skill_name: str) -> ProcessRun:
        """Parse the reply into a ProcessRun; call after ``join``."""
        rc = self._proc.returncode
        assert rc is not None, "finish() before the process exited"
        response = parse_response(self._readers.stdout, skill_name)
        return ProcessRun(response=response, exit_code=rc, pid=self._proc.pid, stderr_tail=self._readers.stderr_tail)


@dataclass(frozen=True)
class UtilityRun:
    exit_code: int
    response: SkillResponse | None
    stderr_tail: str | None


def run_utility(utility: Utility, env: dict[str, str], cwd: Path) -> UtilityRun:
    """Run a utility module (never registered as current) with its descriptor's policy name and timeout; a
    timeout kills its process group. Raises OSError if it cannot start (docs/safety.md)."""
    policy = utility.policy
    proc = spawn(utility.module, {}, env, cwd)
    try:
        out, err = proc.communicate(timeout=policy.timeout_s({}))
    except subprocess.TimeoutExpired:
        killpg(proc.pid)
        out, err = proc.communicate()
    return UtilityRun(
        exit_code=proc.returncode, response=parse_response(out or "", policy.name), stderr_tail=stderr_tail(err)
    )
