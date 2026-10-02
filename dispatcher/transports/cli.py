"""``go2`` command line (docs/running.md)."""

from __future__ import annotations

import argparse
import atexit
import json
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from skills import stub

from .. import process_lock, prompts
from ..config import config_error_exit
from ..dispatcher import Dispatcher
from ..executor import Executor
from ..llm import plan_tool_schema
from ..models import RegistryError, TaskOutcome
from ..registry import Registry, registry_hash
from . import build_dispatcher, format_outcome, load_config_and_env

SOURCE = "cli"
JOIN_POLL_S = 0.2  # main thread join period, so signals are handled (docs/running.md)
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_SIGINT = 130
EXIT_SIGTERM = 143
STOPPING_TEXT = "Stopping..."
COMMENT_PREFIX = "#"
TOOL_SCHEMA_HEADER = "## Tool schema"
REGISTRY_HASH_LINE = "Registry hash: {hash}"
BATCH_TASK_LINE = "Task {i}: {task}"


# --- arguments -------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="go2", description="Go2 LLM dispatcher command line.")
    p.add_argument("--config", type=Path, default=None, help="config file (default ./config.toml)")
    p.add_argument("--backend", choices=("stub", "real"), help="override robot.backend")
    p.add_argument("--horizon", type=int, help="override loop.planning_horizon")
    p.add_argument(
        "--fault",
        action="append",
        metavar="STEP:KIND",
        help="inject a stub fault at dispatched step STEP (repeatable; replaces stub.faults)",
    )
    p.add_argument(
        "--reset-stub", action="store_true", help="reset the stub state file to stub.initial_posture and exit"
    )
    sub = p.add_subparsers(dest="command", metavar="COMMAND")
    run = sub.add_parser("run", help="run one task")
    run.add_argument("task", metavar="TASK")
    batch = sub.add_parser("batch", help="run one task per line of a file")
    batch.add_argument("tasks_file", type=Path, metavar="TASKS_FILE")
    sub.add_parser("catalog", help="print system text, catalog, tool schema and registry hash")
    sub.add_parser("state", help="read the robot state and print it as JSON")
    bot = sub.add_parser("bot", add_help=False, help="run the Telegram bot (go2 bot [--config PATH]; docs/running.md)")
    bot.add_argument("bot_args", nargs=argparse.REMAINDER)
    return p


def _parse_fault(text: str) -> dict[str, Any]:
    step, sep, kind = text.partition(":")
    try:
        if not sep:
            raise ValueError
        return {"step": int(step), "kind": kind}
    except ValueError:
        config_error_exit(f"--fault must be STEP:KIND, got {text!r}")


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if args.backend is not None:
        out["robot.backend"] = args.backend
    if args.horizon is not None:
        out["loop.planning_horizon"] = args.horizon
    if args.fault:
        out["stub.faults"] = [_parse_fault(f) for f in args.fault]
    return out


# --- commands without a dispatcher ------------------------------------------------------


def cmd_catalog(cfg) -> int:
    try:
        registry = Registry.load(cfg.skills.dir)
    except RegistryError as e:
        print(f"Registry error: {e}", file=sys.stderr)
        return EXIT_USAGE
    horizon = cfg.loop.planning_horizon
    catalog = registry.catalog_text()
    system = prompts.system_blocks(horizon, catalog)
    schema = plan_tool_schema(horizon)
    print(system[0])
    print()
    print(system[1])
    print()
    print(TOOL_SCHEMA_HEADER)
    print(json.dumps(schema, indent=2))
    print()
    print(REGISTRY_HASH_LINE.format(hash=registry_hash(system[0], catalog, schema)))
    return EXIT_OK


def cmd_state(cfg) -> int:
    state = Executor(cfg, cfg.base_dir).read_state()
    if state is None:
        print("Robot state unavailable.", file=sys.stderr)
        return EXIT_FAIL
    print(state.model_dump_json(indent=2))
    return EXIT_OK


def cmd_reset_stub(cfg) -> int:
    if cfg.robot.backend != "stub":
        print('--reset-stub needs robot.backend = "stub".', file=sys.stderr)
        return EXIT_USAGE
    process_lock.acquire(cfg.log.dir)
    stub.write_posture(cfg.stub.initial_posture, cfg.stub.state_file)
    print(f"Stub reset: {cfg.stub.initial_posture} ({cfg.stub.state_file}).")
    return EXIT_OK


# --- running tasks ----------------------------------------------------------------------


class _Runner:
    """Runs tasks in a worker thread; the main thread waits and handles signals."""

    def __init__(self, dispatcher: Dispatcher):
        self.d = dispatcher
        self.stop_requested = False

    def install(self) -> None:
        signal.signal(signal.SIGINT, self._on_sigint)
        signal.signal(signal.SIGTERM, self._on_sigterm)

    def _hard_exit(self, code: int) -> None:
        atexit.unregister(self.d.shutdown)
        self.d.shutdown(0)
        raise SystemExit(code)

    def _on_sigint(self, signum, frame) -> None:
        if not self.stop_requested and self.d.is_busy():
            self.stop_requested = True
            self.d.request_stop(SOURCE)
            print(STOPPING_TEXT, flush=True)
            return
        self._hard_exit(EXIT_SIGINT)

    def _on_sigterm(self, signum, frame) -> None:
        self._hard_exit(EXIT_SIGTERM)

    def run(self, task: str) -> TaskOutcome:
        box: dict[str, Any] = {}

        def work() -> None:
            try:
                box["outcome"] = self.d.run_task(task, source=SOURCE)
            except BaseException as e:  # noqa: BLE001 - re-raised in the main thread
                box["error"] = e

        t = threading.Thread(target=work, name="go2-task", daemon=True)
        t.start()
        while t.is_alive():
            t.join(JOIN_POLL_S)
        if "error" in box:
            raise box["error"]
        return box["outcome"]


def _print_outcome(outcome: TaskOutcome, registry: Registry) -> None:
    print(format_outcome(outcome, registry), flush=True)


def cmd_run(cfg, task: str) -> int:
    if not task.strip():
        print(prompts.EMPTY_TASK, file=sys.stderr)
        return EXIT_USAGE
    d = build_dispatcher(cfg, need_llm=True, reset_stub=True)
    runner = _Runner(d)
    runner.install()
    outcome = runner.run(task)
    _print_outcome(outcome, d.registry)
    return EXIT_OK if outcome.outcome == "DONE" else EXIT_FAIL


def read_tasks(path: Path) -> list[str]:
    """Non-blank lines that do not start with ``#``, stripped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith(COMMENT_PREFIX)]


def cmd_batch(cfg, tasks_file: Path) -> int:
    try:
        tasks = read_tasks(tasks_file)
    except OSError as e:
        print(f"Cannot read {tasks_file}: {e}", file=sys.stderr)
        return EXIT_USAGE
    d = build_dispatcher(cfg, need_llm=True, reset_stub=True)
    runner = _Runner(d)
    runner.install()
    for i, task in enumerate(tasks, 1):
        if i > 1:
            print()
        print(BATCH_TASK_LINE.format(i=i, task=task), flush=True)
        _print_outcome(runner.run(task), d.registry)
        if runner.stop_requested:
            return EXIT_SIGINT
    return EXIT_OK


# --- entry point ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["bot"]:
        from . import telegram_bot  # lazy: keeps the Telegram library out of other commands

        return telegram_bot.main(argv[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "bot":
        parser.error("bot takes no global options: go2 bot [--config PATH]")
    if args.reset_stub and args.command is not None:
        parser.error("--reset-stub takes no command")
    if not args.reset_stub and args.command is None:
        parser.error("a command is required (run, batch, catalog, state) or --reset-stub")
    cfg = load_config_and_env(args.config, _overrides(args))
    if args.reset_stub:
        return cmd_reset_stub(cfg)
    if args.command == "catalog":
        return cmd_catalog(cfg)
    if args.command == "state":
        return cmd_state(cfg)
    if args.command == "run":
        return cmd_run(cfg, args.task)
    return cmd_batch(cfg, args.tasks_file)


if __name__ == "__main__":
    sys.exit(main())
