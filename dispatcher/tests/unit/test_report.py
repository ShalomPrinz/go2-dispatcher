"""``go2 report`` through the CLI (analysis/docs/report.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dispatcher.runlog.format import INDEX_FILE
from dispatcher.transports import cli


def write_task(folder: Path, name: str, total_ms: float, duration_ms: float, *, model: str = "m1") -> None:
    """One indexed task with a single answered call; the report's own cases are in analysis/tests."""
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "task_start", "t_mono_ms": 0.0},
        {"type": "llm_request", "call_index": 1, "return_reason": "initial"},
        {
            "type": "llm_response",
            "call_index": 1,
            "total_ms": total_ms,
            "latency_ms": total_ms,
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        },
        {"type": "task_end", "duration_ms": duration_ms},
    ]
    (folder / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    row = {
        "file": f"{name}.jsonl",
        "ts_start": "2026-10-29T15:00:00+03:00",
        "outcome": "DONE",
        "condition": "",
        "provider": "openrouter",
        "model": model,
        "registry_hash": "h1",
    }
    with (folder / INDEX_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def test_model_filter_prints_only_that_model(tmp_path, capsys):
    write_task(tmp_path, "a", 1000.0, 1000.0, model="m1")
    write_task(tmp_path, "b", 9000.0, 9000.0, model="m2")
    assert cli.main(["report", str(tmp_path), "--model", "m2"]) == 0
    out = capsys.readouterr().out
    assert " | model m2" in out and "Model m1" not in out


def test_model_filter_without_a_match_exits_non_zero(tmp_path, capsys):
    write_task(tmp_path, "a", 1000.0, 1000.0, model="m1")
    assert cli.main(["report", str(tmp_path), "--model", "M1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "model M1" in captured.err


def test_cli_prints_seconds_and_exits_non_zero_without_logs(tmp_path, capsys):
    write_task(tmp_path / "logs", "a", 4500.0, 5000.0)
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
