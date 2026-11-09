"""Latency report over one folder of run logs, read-only (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, cast

from .config import Provider
from .runlog import FILE_TIME_FORMAT, INDEX_FILE, STREAM_SUFFIX, TOKEN_KEYS

STUDY_FOLDER = Path("anthropic") / "real"  # under log.dir (dispatcher/docs/run-log.md)
TARGET_CALL_MS = 4000.0  # the advisor's per-call target (dispatcher/docs/run-log.md)
CALL_TYPES = ("initial", "plan_complete", "checkpoint", "failure", "schema_retry")
FIRST_CALL_TYPE = "initial"
REPLAN_TYPES = frozenset({"plan_complete", "checkpoint", "failure"})
# by what the skill physically does, not its budget cost; a test keeps these in step with the catalog
MOTION_SKILLS = frozenset({"walk", "turn", "sit", "stretch"})
STATIONARY_SKILLS = frozenset({"detect_object"})
INDEX_REQUIRED = ("file", "ts_start", "outcome", "provider", "model", "registry_hash")
TS_FILE_PREFIX_LEN = 15  # YYYYMMDDTHHMMSS at the start of a task log's name
DATE_ONLY_LEN = 10  # YYYY-MM-DD

SKIP_UNREADABLE = "log file missing or unreadable"
SKIP_NO_END = "no task_end"
SKIP_NO_RESPONSE = "no model response"
SKIP_MISSING_FIELDS = "older log, fields missing"


class NoLogs(Exception):
    """The folder, or the date window, holds no task to report on."""


class _Skip(Exception):
    """A task whose records cannot be measured; its one argument is the reason."""


@dataclass(frozen=True)
class _Call:
    reason: str
    total_ms: float
    latency_ms: float
    input_tokens: float | None
    output_tokens: float | None


@dataclass(frozen=True)
class _Task:
    duration_ms: float
    calls: list[_Call]
    unanswered: int  # requests that ended in llm_error or llm_interrupted, so with no total_ms
    replans: int
    retries: int
    first_motion_ms: float | None
    motion_ms: float
    non_motion_ms: float
    unknown_skills: frozenset[str]  # in neither skill set; their time counts as non-motion

    @property
    def model_wait_ms(self) -> float:
        return sum(c.total_ms for c in self.calls)


@dataclass(frozen=True)
class Distribution:
    n: int
    median: float | None
    p90: float | None


@dataclass(frozen=True)
class CallRow:
    count: int
    total: Distribution
    max_total_ms: float | None
    median_latency_ms: float | None
    share_under_target: float | None
    median_input_tokens: float | None
    median_output_tokens: float | None


@dataclass(frozen=True)
class TimeSplit:
    total_ms: float
    model_ms: float
    motion_ms: float
    non_motion_ms: float
    other_ms: float  # the remainder, never negative
    overshoot_ms: float  # how far the records exceed the task time; 0 when they do not


@dataclass(frozen=True)
class GroupKey:
    model: str
    registry_hash: str
    condition: str


@dataclass(frozen=True)
class Group:
    key: GroupKey
    providers: list[str]
    first_start: datetime
    last_start: datetime
    outcomes: Counter[str]  # every task in the group, measured or not
    skipped: Counter[str]  # reason -> tasks
    measured: int
    by_type: dict[str, CallRow]
    first_call: CallRow
    later_calls: CallRow
    all_calls: CallRow
    unanswered_calls: int
    duration: Distribution
    first_motion: Distribution
    no_motion_tasks: int
    unknown_skills: list[str]
    model_wait: Distribution
    calls_per_task: Distribution
    replans_per_task: Distribution
    retries_per_task: Distribution
    retries_total: int
    split: TimeSplit

    @property
    def tasks(self) -> int:
        return sum(self.outcomes.values())


@dataclass(frozen=True)
class Report:
    folder: Path
    since: datetime | None
    until: datetime | None
    groups: list[Group]
    unindexed_files: int  # task logs with no index row: the process died before task_end
    bad_index_lines: int


@dataclass
class _Bucket:
    """One group's index rows and parsed tasks while the report is built."""

    providers: set[str] = field(default_factory=set)
    starts: list[datetime] = field(default_factory=list)
    outcomes: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)
    tasks: list[_Task] = field(default_factory=list)


# --- statistics -------------------------------------------------------------------------


def percentile_90(values: Sequence[float]) -> float:
    """Linear interpolation between ranks, numpy's default, so a later baseline can reproduce it."""
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def _distribution(values: Sequence[float]) -> Distribution:
    if not values:
        return Distribution(0, None, None)
    return Distribution(len(values), statistics.median(values), percentile_90(values))


def _median(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def _call_row(calls: Sequence[_Call]) -> CallRow:
    totals = [c.total_ms for c in calls]
    return CallRow(
        count=len(calls),
        total=_distribution(totals),
        max_total_ms=max(totals) if totals else None,
        median_latency_ms=_median(c.latency_ms for c in calls),
        share_under_target=sum(t < TARGET_CALL_MS for t in totals) / len(totals) if totals else None,
        median_input_tokens=_median(c.input_tokens for c in calls),
        median_output_tokens=_median(c.output_tokens for c in calls),
    )


def _time_split(tasks: Sequence[_Task]) -> TimeSplit:
    total = sum(t.duration_ms for t in tasks)
    model = sum(t.model_wait_ms for t in tasks)
    motion = sum(t.motion_ms for t in tasks)
    non_motion = sum(t.non_motion_ms for t in tasks)
    remainder = total - model - motion - non_motion
    return TimeSplit(total, model, motion, non_motion, max(remainder, 0.0), max(-remainder, 0.0))


def _group(key: GroupKey, b: _Bucket) -> Group:
    calls = [c for t in b.tasks for c in t.calls]
    first_motions = [t.first_motion_ms for t in b.tasks if t.first_motion_ms is not None]
    return Group(
        key=key,
        providers=sorted(b.providers),
        first_start=min(b.starts),
        last_start=max(b.starts),
        outcomes=b.outcomes,
        skipped=b.skipped,
        measured=len(b.tasks),
        by_type={kind: _call_row([c for c in calls if c.reason == kind]) for kind in CALL_TYPES},
        first_call=_call_row([c for c in calls if c.reason == FIRST_CALL_TYPE]),
        later_calls=_call_row([c for c in calls if c.reason != FIRST_CALL_TYPE]),
        all_calls=_call_row(calls),
        unanswered_calls=sum(t.unanswered for t in b.tasks),
        duration=_distribution([t.duration_ms for t in b.tasks]),
        first_motion=_distribution(first_motions),
        no_motion_tasks=len(b.tasks) - len(first_motions),
        unknown_skills=sorted({s for t in b.tasks for s in t.unknown_skills}),
        model_wait=_distribution([t.model_wait_ms for t in b.tasks]),
        calls_per_task=_distribution([len(t.calls) for t in b.tasks]),
        replans_per_task=_distribution([t.replans for t in b.tasks]),
        retries_per_task=_distribution([t.retries for t in b.tasks]),
        retries_total=sum(t.retries for t in b.tasks),
        split=_time_split(b.tasks),
    )


# --- reading logs -----------------------------------------------------------------------


def parse_bound(text: str, *, end_of_day: bool) -> datetime:
    """A ``--since``/``--until`` value, ``YYYY-MM-DD`` or a local ISO time without offset; ``ValueError`` if bad."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is not None:
        raise ValueError("give the local time without an offset")
    if end_of_day and len(text) == DATE_ONLY_LEN:
        return datetime.combine(value.date(), time.max)
    return value


def _in_window(start: datetime, since: datetime | None, until: datetime | None) -> bool:
    return (since is None or start >= since) and (until is None or start <= until)


def _token(usage: Any, key: str | None) -> float | None:
    value = (usage or {}).get(key) if key is not None else None
    return float(value) if value is not None else None


def _parse_task(records: list[dict[str, Any]], provider: str) -> _Task:
    """Raises ``_Skip``, or a lookup or type error when a record lacks a field."""
    input_key, output_key = TOKEN_KEYS.get(cast(Provider, provider), (None, None))
    started = False
    duration_ms: float | None = None
    reasons: dict[int, str] = {}
    calls: list[_Call] = []
    retries = 0
    first_motion_ms: float | None = None
    motion_ms = 0.0
    non_motion_ms = 0.0
    unknown_skills: set[str] = set()
    for rec in records:
        kind = rec["type"]
        if kind == "task_start":
            started = True
        elif kind == "llm_request":
            reasons[rec["call_index"]] = rec["return_reason"]
        elif kind == "llm_retry":
            retries += 1
        elif kind == "llm_response":
            usage = rec["usage"]
            calls.append(
                _Call(
                    reason=reasons[rec["call_index"]],
                    total_ms=float(rec["total_ms"]),
                    latency_ms=float(rec["latency_ms"]),
                    input_tokens=_token(usage, input_key),
                    output_tokens=_token(usage, output_key),
                )
            )
        elif kind == "step_start":
            if first_motion_ms is None and rec["ref"]["skill"] in MOTION_SKILLS:
                first_motion_ms = float(rec["t_mono_ms"])
        elif kind == "step_result":
            dispatch = rec["dispatch"]  # null for a rejected step, which ran nothing
            if dispatch is None:
                continue
            skill = rec["ref"]["skill"]
            if skill in MOTION_SKILLS:
                motion_ms += float(rec["duration_ms"])
            else:
                non_motion_ms += float(rec["duration_ms"])
                if skill not in STATIONARY_SKILLS:
                    unknown_skills.add(skill)
        elif kind == "task_end":
            duration_ms = float(rec["duration_ms"])
    if duration_ms is None:
        raise _Skip(SKIP_NO_END)
    if not started:
        raise _Skip(SKIP_MISSING_FIELDS)
    if not calls:
        raise _Skip(SKIP_NO_RESPONSE)
    return _Task(
        duration_ms=duration_ms,
        calls=calls,
        unanswered=len(reasons) - len(calls),
        replans=sum(r in REPLAN_TYPES for r in reasons.values()),
        retries=retries,
        first_motion_ms=first_motion_ms,
        motion_ms=motion_ms,
        non_motion_ms=non_motion_ms,
        unknown_skills=frozenset(unknown_skills),
    )


def _read_task(path: Path, provider: str) -> _Task:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):  # a crash can truncate the last line
        raise _Skip(SKIP_UNREADABLE) from None
    try:
        return _parse_task(records, provider)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _Skip(SKIP_MISSING_FIELDS) from None


def _read_index(folder: Path) -> tuple[list[tuple[datetime, dict[str, Any]]], int]:
    """The index's rows with their local start time, and the number of lines that cannot be used."""
    try:
        lines = (folder / INDEX_FILE).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return [], 0
    rows: list[tuple[datetime, dict[str, Any]]] = []
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not all(isinstance(row.get(k), str) for k in INDEX_REQUIRED):
                raise ValueError
            rows.append((datetime.fromisoformat(row["ts_start"]).replace(tzinfo=None), row))
        except (ValueError, AttributeError):  # older rows, a truncated line, a line that is not an object
            bad += 1
    return rows, bad


def _task_files(folder: Path) -> list[Path]:
    """Task logs by name; only used to find the ones the index does not list."""
    return [
        p
        for p in sorted(folder.glob("*.jsonl"))
        if p.name != INDEX_FILE and not p.name.endswith(STREAM_SUFFIX) and p.is_file()
    ]


def _file_start(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.name[:TS_FILE_PREFIX_LEN], FILE_TIME_FORMAT)
    except ValueError:
        return None


def build(folder: Path, since: datetime | None = None, until: datetime | None = None) -> Report:
    """One report for ``folder``, one group per model, registry hash and condition. Raises ``NoLogs``."""
    rows, bad_lines = _read_index(folder)
    files = _task_files(folder)
    if not rows and not files:
        raise NoLogs(f"No logs in {folder}.")
    indexed = {row["file"] for _, row in rows}
    unindexed = 0
    for path in files:
        start = _file_start(path)
        if path.name not in indexed and (start is None or _in_window(start, since, until)):
            unindexed += 1
    buckets: dict[GroupKey, _Bucket] = {}
    for start, row in rows:
        if not _in_window(start, since, until):
            continue
        key = GroupKey(row["model"], row["registry_hash"], row.get("condition") or "")
        bucket = buckets.setdefault(key, _Bucket())
        bucket.providers.add(row["provider"])
        bucket.starts.append(start)
        bucket.outcomes[row["outcome"]] += 1
        try:
            bucket.tasks.append(_read_task(folder / row["file"], row["provider"]))
        except _Skip as e:
            bucket.skipped[str(e)] += 1
    if not buckets:
        detail = f" ({unindexed} log files have no index row)" if unindexed else ""
        raise NoLogs(f"No finished tasks to report in {folder} for the selected dates{detail}.")
    groups = sorted((_group(k, b) for k, b in buckets.items()), key=lambda g: g.first_start)
    return Report(folder, since, until, groups, unindexed, bad_lines)


# --- rendering --------------------------------------------------------------------------


def _s(ms: float | None) -> str:
    return "-" if ms is None else f"{ms / 1000:.1f}"


def _n(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def _x(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole > 0 else "-"


def _call_line(label: str, r: CallRow) -> str:
    share = "-" if r.share_under_target is None else f"{100 * r.share_under_target:.0f}%"
    return (
        f"  {label:<14}{r.count:>6}{_s(r.total.median):>8}{_s(r.total.p90):>8}{_s(r.max_total_ms):>8}"
        f"{_s(r.median_latency_ms):>9}{share:>7}{_n(r.median_input_tokens):>8}{_n(r.median_output_tokens):>8}"
    )


def _task_line(label: str, d: Distribution, fmt: Callable[[float | None], str]) -> str:
    return f"  {label:<24}{d.n:>5}{fmt(d.median):>9}{fmt(d.p90):>9}"


def _render_group(g: Group) -> list[str]:
    time_format = "%Y-%m-%d %H:%M"
    skipped = sum(g.skipped.values())
    skip_text = ", ".join(f"{n} {reason}" for reason, n in sorted(g.skipped.items()))
    lines = [
        f"Model {g.key.model} | registry {g.key.registry_hash} | condition {g.key.condition or '(none)'}",
        f"{g.tasks} tasks | {g.first_start.strftime(time_format)} to {g.last_start.strftime(time_format)}"
        f" | provider {', '.join(g.providers)}",
        f"Measured {g.measured} tasks, skipped {skipped}" + (f" ({skip_text})" if skipped else ""),
        "",
        f"Calls in seconds; median and p90 and max are total_ms, latency is the median latency_ms; "
        f"target {TARGET_CALL_MS / 1000:.0f} s",
        f"  {'call type':<14}{'count':>6}{'median':>8}{'p90':>8}{'max':>8}{'latency':>9}{'<4s':>7}{'in tok':>8}"
        f"{'out tok':>8}",
    ]
    lines += [_call_line(kind, g.by_type[kind]) for kind in CALL_TYPES]
    lines += [
        _call_line("first call", g.first_call),
        _call_line("later calls", g.later_calls),
        _call_line("all calls", g.all_calls),
    ]
    if g.unanswered_calls:
        lines.append(f"  {g.unanswered_calls} calls got no response (error or stop): not in these rows, no total_ms")
    lines += [
        "",
        f"  {'per task':<24}{'n':>5}{'median':>9}{'p90':>9}",
        _task_line("duration (s)", g.duration, _s),
        _task_line("first motion (s)", g.first_motion, _s),
        _task_line("model wait (s)", g.model_wait, _s),
        _task_line("calls", g.calls_per_task, _x),
        _task_line("replans", g.replans_per_task, _x),
        _task_line("infra retries", g.retries_per_task, _x),
        f"  Tasks with no motion, not in first motion: {g.no_motion_tasks}",
        f"  Infra retries in total: {g.retries_total}",
        "  Outcomes, all tasks: " + ", ".join(f"{k} {v}" for k, v in sorted(g.outcomes.items())),
        "",
    ]
    sp = g.split
    lines.append(f"Time split over {_s(sp.total_ms)} s of measured tasks")
    for label, ms in (
        ("model wait", sp.model_ms),
        ("motion skills", sp.motion_ms),
        ("non-motion skills", sp.non_motion_ms),
        ("other", sp.other_ms),
    ):
        lines.append(f"  {label:<20}{_s(ms):>9} s{_pct(ms, sp.total_ms):>8}")
    if sp.overshoot_ms > 0:
        lines.append(f"  WARNING: the records exceed the task time by {_s(sp.overshoot_ms)} s; other is shown as 0.")
    if g.unknown_skills:
        lines.append(f"  WARNING: unknown skills counted as non-motion: {', '.join(g.unknown_skills)}.")
    return lines


def render(report: Report) -> str:
    window = ""
    if report.since is not None or report.until is not None:
        window = f" | since {report.since or '-'} until {report.until or '-'}"
    lines = [f"Latency report: {report.folder}{window}"]
    if report.unindexed_files:
        lines.append(f"Skipped {report.unindexed_files} log files with no index row (the process died mid-task).")
    if report.bad_index_lines:
        lines.append(f"Skipped {report.bad_index_lines} index lines that cannot be read.")
    for group in report.groups:
        lines += ["", *_render_group(group)]
    return "\n".join(lines)
