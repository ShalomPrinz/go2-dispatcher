"""Shared skill helpers: the per-skill dispatcher policy, stdout capture, response building,
emit, run_main and run_skill, orphan watchdog (skills/docs/skills.md). Standard library only."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import NoReturn

from skills.schema import SCHEMA_VERSION, RobotState, SkillError, SkillResponse, Status

ERROR_MESSAGE_MAX_CHARS = 300
WATCHDOG_POLL_S = 0.2
ORPHAN_GRACE_S = 2.0
ORPHAN_EXIT_CODE = 137
PARENT_PID_ENV = "GO2_PARENT_PID"

Body = Callable[[dict], tuple[Status, dict, "str | None", "str | None", dict]]
# returns (status, observations, error_code, error_message, timing) where timing has init_ms, exec_ms


# --- policy -------------------------------------------------------------------


@dataclass(frozen=True)
class MotionCost:
    distance_m: float = 0.0
    rotation_deg: float = 0.0

    def __post_init__(self):  # plan params may be ints; the run log records floats
        object.__setattr__(self, "distance_m", float(self.distance_m))
        object.__setattr__(self, "rotation_deg", float(self.rotation_deg))


@dataclass(frozen=True)
class SkillPolicy:
    """Per-skill dispatcher policy, one module-level ``POLICY`` per skill (skills/docs/skills.md).
    ``timeout`` and ``cost`` are a constant or a function of the validated params; the dispatcher
    reads them only through ``timeout_s(params)`` and ``motion_cost(params)``."""

    name: str
    timeout: float | Callable[[dict], float]
    cost: MotionCost | Callable[[dict], MotionCost] = MotionCost()
    context_observations: tuple[str, ...] = ()  # observation keys shown to the LLM on ok

    def timeout_s(self, params: dict) -> float:
        return self.timeout(params) if callable(self.timeout) else self.timeout

    def motion_cost(self, params: dict) -> MotionCost:
        return self.cost(params) if callable(self.cost) else self.cost


_saved_stdout_fd: int | None = None
ORPHANED = False
_watchdog_started = False


# --- stdout capture -----------------------------------------------------------


def capture_stdout() -> None:
    """Call first in main(). Duplicate fd 1 to a saved fd, then dup2 fd 2 onto fd 1,
    so anything printed by the SDK, cyclonedds, ultralytics or C code goes to stderr.
    emit() writes only to the saved fd."""
    global _saved_stdout_fd
    if _saved_stdout_fd is not None:
        return
    try:
        sys.stdout.flush()
    except Exception:
        pass
    _saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)


def _out_fd() -> int:
    return _saved_stdout_fd if _saved_stdout_fd is not None else 1


def write_raw_stdout(text: str) -> None:
    """Write text to the saved stdout fd. Used only by the stub's `garbage` fault."""
    data = text.encode("utf-8")
    fd = _out_fd()
    while data:
        n = os.write(fd, data)
        data = data[n:]


# --- orphan watchdog ----------------------------------------------------------


def orphaned() -> bool:
    return ORPHANED


def _watchdog(parent_pid: int) -> None:
    global ORPHANED
    while os.getppid() == parent_pid:
        time.sleep(WATCHDOG_POLL_S)
    ORPHANED = True
    print(f"orphan watchdog: parent {parent_pid} is gone; exiting in {ORPHAN_GRACE_S:g}s", file=sys.stderr, flush=True)
    time.sleep(ORPHAN_GRACE_S)
    os._exit(ORPHAN_EXIT_CODE)


def start_orphan_watchdog() -> None:
    """Start a daemon thread that polls os.getppid() every 0.2 s. If it differs from
    int(os.environ["GO2_PARENT_PID"]), set the module flag ORPHANED; if the process is
    still alive 2.0 s later, os._exit(137). Motion loops check orphaned() each iteration
    and break (then StopMove). Does nothing if GO2_PARENT_PID is unset (manual runs)."""
    global _watchdog_started
    raw = os.environ.get(PARENT_PID_ENV, "").strip()
    if not raw or _watchdog_started:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        print(f"orphan watchdog: ignoring invalid {PARENT_PID_ENV}={raw!r}", file=sys.stderr)
        return
    _watchdog_started = True
    threading.Thread(target=_watchdog, args=(parent_pid,), name="orphan-watchdog", daemon=True).start()


# --- response -----------------------------------------------------------------


def one_line(text: str, limit: int = ERROR_MESSAGE_MAX_CHARS) -> str:
    """Collapse all whitespace (including newlines) to single spaces; cut to ``limit``."""
    return " ".join(str(text).split())[:limit]


def build_response(
    skill: str,
    status: Status,
    *,
    observations: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    state_before: RobotState | None = None,
    state_after: RobotState | None = None,
    state_error: str | None = None,
    timing: dict | None = None,
) -> SkillResponse:
    """The SkillResponse (schema_version=1); only its error-iff-status rule is checked here,
    the dispatcher validates the rest (skills/docs/skills.md).
    error_message: newlines replaced by spaces, collapsed, cut to 300 chars."""
    error = None
    if status == "error":
        if error_code is None or error_message is None:
            raise ValueError("an error response needs error_code and error_message")
        error = SkillError(code=error_code, message=one_line(error_message))
    return SkillResponse(
        schema_version=SCHEMA_VERSION,
        skill=skill,
        status=status,
        observations=dict(observations or {}),
        error=error,
        state_before=state_before,
        state_after=state_after,
        state_error=state_error,
        timing={k: float(v) for k, v in (timing or {}).items()},
    )


def to_json(response: SkillResponse) -> str:
    """The response as one compact JSON line; NaN and infinity raise ValueError."""
    return json.dumps(asdict(response), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def emit(skill, status, **kwargs) -> NoReturn:
    """line = to_json(build_response(...)). Write line + "\\n" to the saved stdout fd, os.fsync it,
    then os._exit(0 if ok else 1).
    os._exit is required: DDS/SDK threads can hang normal interpreter shutdown."""
    try:
        line = to_json(build_response(skill, status, **kwargs))
    except Exception as e:  # a bug in the skill: still emit one valid line
        traceback.print_exc(file=sys.stderr)
        status = "error"
        code, message = error_from_exception(e)
        line = to_json(build_response(skill, status, error_code=code, error_message=message))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    write_raw_stdout(line + "\n")
    try:
        os.fsync(_out_fd())
    except OSError:
        pass  # pipes and terminals cannot be fsynced
    os._exit(0 if status == "ok" else 1)


# --- run_skill ----------------------------------------------------------------


def error_from_exception(e: BaseException) -> tuple[str, str]:
    """(error_code, message) for an unexpected exception in a skill process."""
    from skills.backend import BackendNotConfigured

    if isinstance(e, BackendNotConfigured):
        return "backend_not_configured", str(e)
    first = str(e).splitlines()[0] if str(e) else ""
    return "exception", f"{type(e).__name__}: {first}"


def parse_params(argv: list[str] | None = None) -> dict:
    """Parse argv[1] as a JSON object; raises InvalidParams."""
    argv = sys.argv if argv is None else argv
    if len(argv) < 2:
        raise InvalidParams("missing params argument (expected a JSON object)")
    try:
        params = json.loads(argv[1])
    except json.JSONDecodeError as e:
        raise InvalidParams(f"params are not valid JSON: {e.msg}") from None
    if not isinstance(params, dict):
        raise InvalidParams("params must be a JSON object")
    return params


class InvalidParams(Exception):
    """Raised by parse_params for code ``invalid_params``."""


def ms_since(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000.0, 3)


def sample_state_safe(errors: list[str], label: str) -> tuple[RobotState | None, float]:
    """Sample state; on failure append "<label>: <Type>: <msg>" to errors.
    BackendNotConfigured propagates. Returns (state or None, elapsed seconds)."""
    from skills import backend

    t = time.monotonic()
    try:
        state = backend.sample_state()
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        first = str(e).splitlines()[0] if str(e) else ""
        errors.append(f"{label}: {type(e).__name__}: {first}")
        state = None
    return state, time.monotonic() - t


def run_main(skill: str, body: Callable[[dict], None], *, watchdog: bool = True) -> NoReturn:
    """Shared main() of skills and utilities: capture_stdout(), optional orphan watchdog, then
    body(out), where out holds emit()'s keyword arguments plus "status" and body fills it in place,
    so partial results survive an exception. An exception sets status=error with
    error_response_fields(); timing["total_ms"] is added; then emit()."""
    capture_stdout()
    if watchdog:
        start_orphan_watchdog()
    t0 = time.monotonic()
    out: dict = {"status": "error", "error_code": None, "error_message": None, "timing": {}}
    try:
        body(out)
    except Exception as e:
        out["status"] = "error"
        if isinstance(e, InvalidParams):
            out["error_code"], out["error_message"] = "invalid_params", str(e)
        else:
            out["error_code"], out["error_message"] = error_from_exception(e)
            if out["error_code"] != "backend_not_configured":
                traceback.print_exc(file=sys.stderr)
    out["timing"]["total_ms"] = ms_since(t0)
    emit(skill, out.pop("status"), **out)


def run_skill(policy: SkillPolicy, body: Body, *, sample_state: bool = True) -> NoReturn:
    """Standard main() of a skill, via run_main:
    1. params = json.loads(argv[1]); must be a dict -> else error invalid_params
    2. if sample_state: state_before = backend.sample_state() (exception -> state_error, continue)
    3. status, obs, code, msg, timing = body(params)
    4. if sample_state: state_after = backend.sample_state() (exception -> append to state_error)
    5. timing["state_ms"] = total time spent in the two samples; timing["total_ms"] = since start
    Any exception from steps 1-4 gives status=error, code="exception",
    message="<ExceptionType>: <first line of str(e)>"; the traceback goes to stderr.
    BackendNotConfigured maps to code "backend_not_configured"; InvalidParams to
    "invalid_params"."""

    def main(out: dict) -> None:
        state_errors: list[str] = []
        state_s = 0.0
        try:
            params = parse_params()
            if sample_state:
                out["state_before"], dt = sample_state_safe(state_errors, "before")
                state_s += dt
            status, obs, code, msg, timing = body(params)
            out["timing"] = dict(timing or {})
            if sample_state:
                out["state_after"], dt = sample_state_safe(state_errors, "after")
                state_s += dt
            out.update(status=status, observations=obs, error_code=code, error_message=msg)
        finally:
            out["state_error"] = "; ".join(state_errors) or None
            if sample_state:
                out["timing"]["state_ms"] = round(state_s * 1000.0, 3)

    run_main(policy.name, main)
