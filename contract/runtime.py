"""The process runtime of skills and utilities: SkillOutcome, state sampling, response building, emit, params parsing
and run_main (contract/docs/contract.md). Standard library only."""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal, NoReturn

from contract import backend, process
from contract.schema import (
    SCHEMA_VERSION,
    ErrorCode,
    RobotState,
    SkillError,
    SkillResponse,
    Status,
    describe_exception,
)


@dataclass(frozen=True)
class SkillOutcome:
    """What a skill or utility body returns; build it with ok(), error() or from_exception()
    (contract/docs/contract.md). timing holds the body's own keys, such as exec_ms."""

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
        if isinstance(e, InvalidParams):
            code, message = ErrorCode.INVALID_PARAMS, str(e)
        elif isinstance(e, backend.BackendNotConfigured):
            code, message = ErrorCode.BACKEND_NOT_CONFIGURED, str(e)
        else:
            code, message = ErrorCode.EXCEPTION, describe_exception(e)
        return cls.error(code, message, observations={}, timing={})


StateReader = Callable[[], RobotState]


@dataclass
class StateSampler:
    """The state samples of one process, filled by take() and read by build_response; run_main receives it, so
    samples taken before an exception survive (contract/docs/contract.md). ``reader`` reads one sample;
    ``prepare`` (a connect or DDS init) runs before each sample, outside state_ms. ``clock`` times the reads."""

    reader: StateReader = field(repr=False, compare=False)
    prepare: Callable[[], object] | None = field(default=None, repr=False, compare=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    before: RobotState | None = None
    after: RobotState | None = None
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0
    used: bool = False  # a sample was attempted: timing gets state_ms

    def take(self, label: Literal["before", "after"], *, strict: bool = False) -> RobotState | None:
        """Sample state into ``label``; on failure record "<label>: <Type>: <msg>" and return None.
        strict re-raises instead and records nothing. BackendNotConfigured always propagates."""
        self.used = True
        try:
            backend.check()  # a misconfigured backend fails the call, not just its samples
            if self.prepare is not None:
                self.prepare()
            t = self.clock()
            try:
                state = self.reader()
            finally:
                self.seconds += self.clock() - t
        except backend.BackendNotConfigured:
            raise
        except Exception as e:
            if strict:
                raise
            traceback.print_exc(file=sys.stderr)
            self.errors.append(f"{label}: {describe_exception(e)}")
            state = None
        setattr(self, label, state)
        return state

    def state_error(self) -> str | None:
        return "; ".join(self.errors) or None


# --- response -----------------------------------------------------------------


def one_line(text: str) -> str:
    """Collapse all whitespace (including newlines) to single spaces; the dispatcher owns the length limit."""
    return " ".join(str(text).split())


def build_response(
    skill: str,
    outcome: SkillOutcome,
    states: StateSampler,
    total_ms: float,
    *,
    setup: Mapping[str, float] | None = None,
) -> SkillResponse:
    """The SkillResponse (schema_version=2), built from the sources of its fields; ``setup`` is the process's
    setup timing (robot_connect_ms in a skill, dds_init_ms in a utility), first in timing. Only the
    error-iff-status rule is checked here, the dispatcher validates the rest (contract/docs/contract.md).
    error_message: collapsed to one line, not length-limited."""
    error = None
    if outcome.status == "error":
        if outcome.error_code is None or outcome.error_message is None:
            raise ValueError("an error response needs error_code and error_message")
        error = SkillError(code=str(outcome.error_code), message=one_line(outcome.error_message))
    timing = dict(setup or {})
    timing.update(outcome.timing)
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


def emit(
    skill: str, outcome: SkillOutcome, states: StateSampler, total_ms: float, setup: Mapping[str, float]
) -> NoReturn:
    """line = to_json(build_response(...)). Write line + "\\n" to the saved stdout fd, os.fsync it,
    then os._exit(0 if ok else 1).
    os._exit is required: DDS/SDK threads can hang normal interpreter shutdown."""
    try:
        line = to_json(build_response(skill, outcome, states, total_ms, setup=setup))
    except Exception as e:  # a bug in the body: still emit one valid line, without the body's data
        traceback.print_exc(file=sys.stderr)
        outcome = SkillOutcome.from_exception(e)
        line = to_json(build_response(skill, outcome, StateSampler(states.reader), total_ms))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    process.write_raw_stdout(line + "\n")
    process.fsync_stdout()
    os._exit(0 if outcome.status == "ok" else 1)


# --- main ---------------------------------------------------------------------


class InvalidParams(Exception):
    """Raised by parse_params for code ``invalid_params``."""


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


def ms_since(t0: float) -> float:
    return round((time.monotonic() - t0) * 1000.0, 3)


def run_main(
    skill: str,
    body: Callable[[StateSampler], SkillOutcome],
    *,
    states: StateSampler,
    setup: Callable[[], Mapping[str, float]],
    watchdog: bool = True,
) -> NoReturn:
    """Shared main() of skills and utilities: capture_stdout(), optional orphan watchdog, then
    outcome = body(states). An exception becomes SkillOutcome.from_exception(e); states sampled before it are
    kept. Then emit() with total_ms since start and the ``setup`` timing (robot_connect_ms in a skill,
    dds_init_ms in a utility)."""
    process.capture_stdout()
    if watchdog:
        process.start_orphan_watchdog()
    t0 = time.monotonic()
    try:
        outcome = body(states)
    except Exception as e:
        outcome = SkillOutcome.from_exception(e)
        if outcome.error_code == ErrorCode.EXCEPTION:
            traceback.print_exc(file=sys.stderr)
    emit(skill, outcome, states, ms_since(t0), setup())
