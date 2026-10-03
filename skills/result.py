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
from dataclasses import asdict, dataclass, field
from typing import Literal, NoReturn

from skills.env import PARENT_PID
from skills.schema import SCHEMA_VERSION, ErrorCode, RobotState, SkillError, SkillResponse, Status

ERROR_MESSAGE_MAX_CHARS = 300
WATCHDOG_POLL_S = 0.2
ORPHAN_GRACE_S = 2.0
ORPHAN_EXIT_CODE = 137


@dataclass(frozen=True)
class SkillOutcome:
    """What a skill body returns to run_skill; build it with ok(), error() or from_exception()
    (skills/docs/skills.md). timing holds init_ms and exec_ms."""

    status: Status
    observations: dict
    error_code: ErrorCode | None
    error_message: str | None
    timing: dict

    @classmethod
    def ok(cls, *, observations: dict, timing: dict) -> SkillOutcome:
        return cls("ok", observations, None, None, timing)

    @classmethod
    def error(cls, code: ErrorCode, message: str, *, observations: dict, timing: dict) -> SkillOutcome:
        return cls("error", observations, code, message, timing)

    @classmethod
    def from_exception(cls, e: BaseException) -> SkillOutcome:
        """The error outcome for an exception that escaped a skill or utility body."""
        from skills.backend import BackendNotConfigured

        if isinstance(e, InvalidParams):
            code, message = ErrorCode.INVALID_PARAMS, str(e)
        elif isinstance(e, BackendNotConfigured):
            code, message = ErrorCode.BACKEND_NOT_CONFIGURED, str(e)
        else:
            code, message = ErrorCode.EXCEPTION, describe_exception(e)
        return cls.error(code, message, observations={}, timing={})


@dataclass
class StateSampler:
    """The state samples of one skill process, filled by take() and read by build_response;
    run_main owns it, so samples taken before an exception survive (skills/docs/skills.md)."""

    before: RobotState | None = None
    after: RobotState | None = None
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    used: bool = False  # a sample was attempted: timing gets state_ms

    def take(self, label: Literal["before", "after"], *, strict: bool = False) -> RobotState | None:
        """Sample state into ``label``; on failure record "<label>: <Type>: <msg>" and return None.
        strict re-raises instead and records nothing. BackendNotConfigured always propagates."""
        from skills import backend

        self.used = True
        t = time.monotonic()
        try:
            state = backend.sample_state()
        except backend.BackendNotConfigured:
            raise
        except Exception as e:
            if strict:
                raise
            traceback.print_exc(file=sys.stderr)
            self.errors.append(f"{label}: {describe_exception(e)}")
            state = None
        finally:
            self.seconds += time.monotonic() - t
        setattr(self, label, state)
        return state

    def state_error(self) -> str | None:
        return "; ".join(self.errors) or None


def describe_exception(e: BaseException) -> str:
    """Return "<ExceptionType>: <first line of str(e)>"."""
    first = str(e).splitlines()[0] if str(e) else ""
    return f"{type(e).__name__}: {first}"


Body = Callable[[dict], SkillOutcome]


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
    raw = os.environ.get(PARENT_PID, "").strip()
    if not raw or _watchdog_started:
        return
    try:
        parent_pid = int(raw)
    except ValueError:
        print(f"orphan watchdog: ignoring invalid {PARENT_PID}={raw!r}", file=sys.stderr)
        return
    _watchdog_started = True
    threading.Thread(target=_watchdog, args=(parent_pid,), name="orphan-watchdog", daemon=True).start()


# --- response -----------------------------------------------------------------


def one_line(text: str, limit: int = ERROR_MESSAGE_MAX_CHARS) -> str:
    """Collapse all whitespace (including newlines) to single spaces; cut to ``limit``."""
    return " ".join(str(text).split())[:limit]


def build_response(skill: str, outcome: SkillOutcome, states: StateSampler, total_ms: float) -> SkillResponse:
    """The SkillResponse (schema_version=1), built from the three sources of its fields; only the
    error-iff-status rule is checked here, the dispatcher validates the rest (skills/docs/skills.md).
    error_message: newlines replaced by spaces, collapsed, cut to 300 chars."""
    error = None
    if outcome.status == "error":
        if outcome.error_code is None or outcome.error_message is None:
            raise ValueError("an error response needs error_code and error_message")
        error = SkillError(code=str(outcome.error_code), message=one_line(outcome.error_message))
    timing = dict(outcome.timing)
    if states.used:
        timing["state_ms"] = round(states.seconds * 1000.0, 3)
    timing["total_ms"] = total_ms
    return SkillResponse(
        schema_version=SCHEMA_VERSION,
        skill=skill,
        status=outcome.status,
        observations=dict(outcome.observations),
        error=error,
        state_before=states.before,
        state_after=states.after,
        state_error=states.state_error(),
        timing={k: float(v) for k, v in timing.items()},
    )


def to_json(response: SkillResponse) -> str:
    """The response as one compact JSON line; NaN and infinity raise ValueError."""
    return json.dumps(asdict(response), ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def emit(skill: str, outcome: SkillOutcome, states: StateSampler, total_ms: float) -> NoReturn:
    """line = to_json(build_response(...)). Write line + "\\n" to the saved stdout fd, os.fsync it,
    then os._exit(0 if ok else 1).
    os._exit is required: DDS/SDK threads can hang normal interpreter shutdown."""
    try:
        line = to_json(build_response(skill, outcome, states, total_ms))
    except Exception as e:  # a bug in the skill: still emit one valid line, without the body's data
        traceback.print_exc(file=sys.stderr)
        outcome = SkillOutcome.from_exception(e)
        line = to_json(build_response(skill, outcome, StateSampler(), total_ms))
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
    os._exit(0 if outcome.status == "ok" else 1)


# --- run_skill ----------------------------------------------------------------


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


def run_main(skill: str, body: Callable[[StateSampler], SkillOutcome], *, watchdog: bool = True) -> NoReturn:
    """Shared main() of skills and utilities: capture_stdout(), optional orphan watchdog, then
    outcome = body(states). An exception becomes SkillOutcome.from_exception(e); states sampled
    before it are kept. Then emit() with total_ms since start."""
    capture_stdout()
    if watchdog:
        start_orphan_watchdog()
    t0 = time.monotonic()
    states = StateSampler()
    try:
        outcome = body(states)
    except Exception as e:
        outcome = SkillOutcome.from_exception(e)
        if outcome.error_code == ErrorCode.EXCEPTION:
            traceback.print_exc(file=sys.stderr)
    emit(skill, outcome, states, ms_since(t0))


def run_skill(policy: SkillPolicy, body: Body, *, sample_state: bool = True) -> NoReturn:
    """Standard main() of a skill, via run_main:
    1. params = json.loads(argv[1]); must be a dict -> else error invalid_params
    2. if sample_state: sample state_before (exception -> state_error, continue)
    3. outcome = body(params), a SkillOutcome
    4. if sample_state: sample state_after (exception -> appended to state_error)
    timing gets state_ms (time in the samples, if any was taken) and total_ms.
    Exceptions map as in SkillOutcome.from_exception; the traceback goes to stderr."""

    def main(states: StateSampler) -> SkillOutcome:
        params = parse_params()
        if sample_state:
            states.take("before")
        outcome = body(params)
        if sample_state:
            states.take("after")
        return outcome

    run_main(policy.name, main)
