"""Reading one folder of run logs: the index and the task files (analysis/docs/report.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from dispatcher.config import Provider
from dispatcher.runlog.format import FILE_TIME_FORMAT, INDEX_FILE, STREAM_SUFFIX, TOKEN_KEYS

REPLAN_TYPES = frozenset({"plan_complete", "checkpoint", "failure"})
INDEX_REQUIRED = ("file", "ts_start", "outcome", "provider", "model", "registry_hash")
TS_FILE_PREFIX_LEN = 15  # YYYYMMDDTHHMMSS at the start of a task log's name

SKIP_UNREADABLE = "log file missing or unreadable"
SKIP_NO_END = "no task_end"
SKIP_NO_RESPONSE = "no model response"
SKIP_MISSING_FIELDS = "older log, fields missing"


class NoLogs(Exception):
    """The folder, the date window or the selected model holds no task to report on."""


class Skip(Exception):
    """A task whose records cannot be measured; its one argument is the reason."""


@dataclass(frozen=True)
class SkillSets:
    """Skill names that move the robot and that do not; a skill in neither counts as non-motion."""

    motion: frozenset[str]
    stationary: frozenset[str]


@dataclass(frozen=True)
class Call:
    reason: str
    total_ms: float
    latency_ms: float
    input_tokens: float | None
    output_tokens: float | None


@dataclass(frozen=True)
class Task:
    duration_ms: float
    calls: list[Call]
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


def _token(usage: Any, key: str | None) -> float | None:
    value = (usage or {}).get(key) if key is not None else None
    return float(value) if value is not None else None


def _parse_task(records: list[dict[str, Any]], provider: str, skills: SkillSets) -> Task:
    """Raises ``Skip``, or a lookup or type error when a record lacks a field."""
    input_key, output_key = TOKEN_KEYS.get(cast(Provider, provider), (None, None))
    started = False
    duration_ms: float | None = None
    reasons: dict[int, str] = {}
    calls: list[Call] = []
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
                Call(
                    reason=reasons[rec["call_index"]],
                    total_ms=float(rec["total_ms"]),
                    latency_ms=float(rec["latency_ms"]),
                    input_tokens=_token(usage, input_key),
                    output_tokens=_token(usage, output_key),
                )
            )
        elif kind == "step_start":
            if first_motion_ms is None and rec["ref"]["skill"] in skills.motion:
                first_motion_ms = float(rec["t_mono_ms"])
        elif kind == "step_result":
            dispatch = rec["dispatch"]  # null for a rejected step, which ran nothing
            if dispatch is None:
                continue
            skill = rec["ref"]["skill"]
            if skill in skills.motion:
                motion_ms += float(rec["duration_ms"])
            else:
                non_motion_ms += float(rec["duration_ms"])
                if skill not in skills.stationary:
                    unknown_skills.add(skill)
        elif kind == "task_end":
            duration_ms = float(rec["duration_ms"])
    if duration_ms is None:
        raise Skip(SKIP_NO_END)
    if not started:
        raise Skip(SKIP_MISSING_FIELDS)
    if not calls:
        raise Skip(SKIP_NO_RESPONSE)
    return Task(
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


def read_task(path: Path, provider: str, skills: SkillSets) -> Task:
    """One task log, measured; raises ``Skip`` with the reason it cannot be."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError):  # a crash can truncate the last line
        raise Skip(SKIP_UNREADABLE) from None
    try:
        return _parse_task(records, provider, skills)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise Skip(SKIP_MISSING_FIELDS) from None


def read_index(folder: Path) -> tuple[list[tuple[datetime, dict[str, Any]]], int]:
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


def task_files(folder: Path) -> list[Path]:
    """Task logs by name; only used to find the ones the index does not list."""
    return [
        p
        for p in sorted(folder.glob("*.jsonl"))
        if p.name != INDEX_FILE and not p.name.endswith(STREAM_SUFFIX) and p.is_file()
    ]


def file_start(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.name[:TS_FILE_PREFIX_LEN], FILE_TIME_FORMAT)
    except ValueError:
        return None
