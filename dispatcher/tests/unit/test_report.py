"""``go2 report`` over small fixture logs (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import importlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from dispatcher import report
from dispatcher.registry import Registry
from dispatcher.transports import cli
from tests.helpers import REPO_ROOT


def request(index: int, reason: str) -> dict[str, Any]:
    return {"type": "llm_request", "call_index": index, "return_reason": reason}


def response(index: int, total: float, *, latency: float | None = None, tokens: tuple[int, int] = (100, 10)) -> dict:
    return {
        "type": "llm_response",
        "call_index": index,
        "total_ms": total,
        "latency_ms": total if latency is None else latency,
        "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
    }


def step(t: float, skill: str, duration: float) -> list[dict[str, Any]]:
    ref = {"skill": skill}
    return [
        {"type": "step_start", "t_mono_ms": t, "ref": ref, "dispatch": {}},
        {"type": "step_result", "ref": ref, "dispatch": {}, "duration_ms": duration},
    ]


def task_end(duration: float) -> dict[str, Any]:
    return {"type": "task_end", "duration_ms": duration}


def write_task(
    folder: Path,
    name: str,
    records: list[dict[str, Any]],
    *,
    ts: str = "2026-10-29T15:00:00+03:00",
    outcome: str = "DONE",
    model: str = "m1",
    registry_hash: str = "h1",
    indexed: bool = True,
) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    file = f"{name}.jsonl"
    with (folder / file).open("w", encoding="utf-8") as f:
        for rec in [{"type": "task_start", "t_mono_ms": 0.0}, *records]:
            f.write(json.dumps(rec) + "\n")
    if indexed:
        row = {
            "file": file,
            "ts_start": ts,
            "outcome": outcome,
            "condition": "",
            "provider": "openrouter",
            "model": model,
            "registry_hash": registry_hash,
        }
        with (folder / report.INDEX_FILE).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def only_group(folder: Path) -> report.Group:
    groups = report.build(folder).groups
    assert len(groups) == 1
    return groups[0]


def test_first_call_is_initial_and_later_calls_are_everything_else(tmp_path):
    write_task(
        tmp_path,
        "a",
        [
            request(1, "initial"),
            {"type": "llm_retry", "call_index": 1},
            response(1, 5000.0, latency=2000.0, tokens=(100, 10)),
            request(2, "schema_retry"),
            response(2, 3000.0, tokens=(300, 30)),
            request(3, "failure"),
            response(3, 9000.0, tokens=(500, 50)),
            task_end(20000.0),
        ],
    )
    g = only_group(tmp_path)
    assert g.first_call.count == 1
    assert g.first_call.total.median == 5000.0  # the operator waits through the retry
    assert g.first_call.median_latency_ms == 2000.0
    assert g.first_call.median_input_tokens == 100  # openrouter's prompt_tokens
    assert g.later_calls.count == 2
    assert g.later_calls.total.median == 6000.0
    assert g.later_calls.max_total_ms == 9000.0
    assert g.later_calls.share_under_target == 0.5
    assert g.later_calls.median_output_tokens == 40
    assert g.by_type["schema_retry"].count == 1
    assert g.by_type["checkpoint"].count == 0
    assert g.all_calls.count == 3
    assert (g.calls_per_task.median, g.replans_per_task.median, g.retries_total) == (3, 1, 1)  # schema_retry no replan


def test_p90_interpolates_between_ranks():
    assert report.percentile_90([float(v) for v in range(1, 12)]) == 10.0
    assert report.percentile_90([0.0, 10.0]) == 9.0
    assert report.percentile_90([7.0]) == 7.0


def test_time_split_and_first_motion(tmp_path):
    write_task(
        tmp_path,
        "a",
        [
            request(1, "initial"),
            response(1, 4000.0),
            *step(4100.0, "detect_object", 500.0),  # a look does not count as motion
            *step(4700.0, "turn", 3000.0),
            task_end(10000.0),
        ],
    )
    write_task(tmp_path, "b", [request(1, "initial"), response(1, 2000.0), task_end(2000.0)])
    g = only_group(tmp_path)
    assert g.split.total_ms == 12000.0
    assert g.split.model_ms == 6000.0
    assert g.split.motion_ms == 3000.0
    assert g.split.non_motion_ms == 500.0
    assert g.split.other_ms == 2500.0
    assert g.split.overshoot_ms == 0.0
    assert g.first_motion.n == 1
    assert g.first_motion.median == 4700.0
    assert g.no_motion_tasks == 1


def test_zero_cost_skills_that_move_the_robot_count_as_motion(tmp_path):
    write_task(
        tmp_path,
        "a",
        [request(1, "initial"), response(1, 1000.0), *step(1100.0, "sit", 2000.0), task_end(3100.0)],
    )
    g = only_group(tmp_path)
    assert g.first_motion.median == 1100.0
    assert g.no_motion_tasks == 0
    assert (g.split.motion_ms, g.split.non_motion_ms) == (2000.0, 0.0)


def test_unknown_skill_counts_as_non_motion_and_is_named(tmp_path):
    write_task(
        tmp_path,
        "a",
        [request(1, "initial"), response(1, 1000.0), *step(1100.0, "dance", 2000.0), task_end(3100.0)],
    )
    r = report.build(tmp_path)
    g = r.groups[0]
    assert g.no_motion_tasks == 1
    assert g.split.non_motion_ms == 2000.0
    assert "unknown skills counted as non-motion: dance" in report.render(r)


def test_skill_sets_match_the_catalog():
    """A skill moves the robot if its module uses ``skills.motion``; a new skill must be classified."""
    registry = Registry.load(REPO_ROOT / "skills" / "catalog")
    for name in registry.names():
        moves = "motion" in vars(importlib.import_module(registry[name].entrypoint))
        assert (name in report.MOTION_SKILLS) == moves, name
        assert (name in report.STATIONARY_SKILLS) == (not moves), name


def test_records_exceeding_the_task_time_are_reported_not_negative(tmp_path):
    write_task(tmp_path, "a", [request(1, "initial"), response(1, 5000.0), task_end(4000.0)])
    r = report.build(tmp_path)
    assert r.groups[0].split.other_ms == 0.0
    assert r.groups[0].split.overshoot_ms == 1000.0
    assert "exceed the task time by 1.0 s" in report.render(r)


def test_partial_tasks_are_skipped_and_counted(tmp_path):
    write_task(tmp_path, "ok", [request(1, "initial"), response(1, 1000.0), task_end(1000.0)])
    write_task(tmp_path, "state", [task_end(10.0)], outcome="STATE_UNAVAILABLE")
    write_task(tmp_path, "no_end", [request(1, "initial")])
    old = response(1, 1000.0)
    del old["total_ms"]
    write_task(tmp_path, "old", [request(1, "initial"), old, task_end(1000.0)])
    write_task(tmp_path, "crash", [request(1, "initial")], indexed=False)
    (tmp_path / "gone.state_stream.jsonl").write_text("{}\n", encoding="utf-8")  # not a task log
    with (tmp_path / report.INDEX_FILE).open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    r = report.build(tmp_path)
    g = r.groups[0]
    assert g.measured == 1
    assert g.tasks == 4
    assert g.skipped == {report.SKIP_NO_RESPONSE: 1, report.SKIP_NO_END: 1, report.SKIP_MISSING_FIELDS: 1}
    assert g.outcomes["STATE_UNAVAILABLE"] == 1
    assert r.unindexed_files == 1
    assert r.bad_index_lines == 1
    assert "skipped 3" in report.render(r)


def test_groups_are_never_pooled_and_dates_filter(tmp_path):
    good = [request(1, "initial"), response(1, 1000.0), task_end(1000.0)]
    write_task(tmp_path, "a", good, registry_hash="h1", ts="2026-10-28T10:00:00+03:00")
    write_task(tmp_path, "b", good, registry_hash="h2", ts="2026-10-29T15:00:00+03:00")
    assert len(report.build(tmp_path).groups) == 2
    day = report.parse_bound("2026-10-29", end_of_day=True)
    since = report.parse_bound("2026-10-29T14:59", end_of_day=False)
    filtered = report.build(tmp_path, since, day)
    assert [g.key.registry_hash for g in filtered.groups] == ["h2"]
    assert report.build(tmp_path, None, report.parse_bound("2026-10-28", end_of_day=True)).groups[0].tasks == 1
    assert day == datetime(2026, 10, 29, 23, 59, 59, 999999)


def test_model_filter_counts_only_that_model(tmp_path, capsys):
    write_task(tmp_path, "a", [request(1, "initial"), response(1, 1000.0), task_end(1000.0)], model="m1")
    write_task(tmp_path, "b", [request(1, "initial"), response(1, 9000.0), task_end(9000.0)], model="m2")
    write_task(tmp_path, "c", [request(1, "initial"), response(1, 7000.0), task_end(7000.0)], model="m1-mini")
    with (tmp_path / report.INDEX_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"file": "a.jsonl", "ts_start": "2026-10-29T15:00:00+03:00"}) + "\n")  # older: no model
    r = report.build(tmp_path, model="m1")
    assert [g.key.model for g in r.groups] == ["m1"]
    assert r.groups[0].all_calls.total.median == 1000.0
    assert r.bad_index_lines == 1  # cannot be tied to a model, so stays visible and is in no group
    assert cli.main(["report", str(tmp_path), "--model", "m2"]) == 0
    out = capsys.readouterr().out
    assert " | model m2" in out and "Model m1" not in out


def test_model_filter_without_a_match_exits_non_zero(tmp_path, capsys):
    write_task(tmp_path, "a", [request(1, "initial"), response(1, 1000.0), task_end(1000.0)], model="m1")
    assert cli.main(["report", str(tmp_path), "--model", "M1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "model M1" in captured.err


def test_cli_prints_seconds_and_exits_non_zero_without_logs(tmp_path, capsys):
    write_task(tmp_path / "logs", "a", [request(1, "initial"), response(1, 4500.0), task_end(5000.0)])
    assert cli.main(["report", str(tmp_path / "logs")]) == 0
    out = capsys.readouterr().out
    assert "1 tasks | 2026-10-29 15:00 to 2026-10-29 15:00 | provider openrouter" in out
    assert "4.5" in out and "4500" not in out

    assert cli.main(["report", str(tmp_path / "empty")]) == 1
    assert "No logs in" in capsys.readouterr().err
    assert cli.main(["report", str(tmp_path / "logs"), "--since", "2027-01-01"]) == 1
    assert "selected dates" in capsys.readouterr().err
    with pytest.raises(SystemExit) as e:
        cli.main(["report", str(tmp_path / "logs"), "--since", "yesterday"])
    assert e.value.code == 2


def test_default_folder_is_the_study_folder_under_log_dir(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text('[log]\ndir = "logs"\n', encoding="utf-8")
    assert cli.main(["--config", str(config), "report"]) == 1
    assert str(tmp_path / "logs" / "anthropic" / "real") in capsys.readouterr().err
