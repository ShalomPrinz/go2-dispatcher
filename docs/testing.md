# Testing

Spec: §19.

## Running the tests

```bash
uv run pytest                    # everything that needs no network, API key, robot or SDK
uv run pytest -q tests/unit      # unit tests only
uv run pytest -m integration     # real subprocesses on the stub backend
uv run pytest -k context         # by name
```

The default run must pass on any machine after `uv sync` (core + dev dependencies only). Every test has a 30 s timeout (`pytest-timeout`).

## Layout

| Folder | What |
|---|---|
| `tests/unit/` | config, process lock, registry, tool schema, plan validation, bounds, precheck, motion budget, policies, render + context, skill response, posture, prompts, LLM client (mock HTTP), dispatcher loop, `format_outcome`, Telegram handlers |
| `tests/integration/` | skill contract and stub behaviour, utilities, orphan watchdog, executor, end-to-end with the real executor, CLI subprocesses, live LLM |
| `tests/golden/` | expected catalog and context texts |
| `tests/helpers/` | shared helpers (below) |
| `tests/robot/` | reserved for opt-in robot tests; the robot checks are the manual checklist below |

## Markers and flags

| Marker | Meaning | Enabled by |
|---|---|---|
| `integration` | spawns real subprocesses with the stub backend | always runs |
| `live_llm` | calls the real Anthropic API | `--run-live` and `ANTHROPIC_API_KEY` |
| `robot` | needs the real Go2 | `--run-robot` |

Options (defined in `tests/conftest.py`):

- `--run-live`: run `live_llm` tests. They are skipped without it, and also skipped if `ANTHROPIC_API_KEY` is not set.
- `--run-robot`: run `robot` tests. There are none in v1; the robot checks are manual (below).
- `--update-golden`: rewrite the golden files instead of comparing against them. Review the diff with `git diff tests/golden` before committing.

### Live LLM test

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest --run-live -s tests/integration/test_live_llm.py
```

It runs the task "turn left 90 degrees, then tell me if you see a chair" on the stub with `stub.detections = {chair = "center:near"}`. It passes if the task ends `DONE`, the message mentions the chair, and the run log has no `plan_invalid` or `horizon_rejection` record. With `-s`, it prints the run log path.

It uses `llm.model` from the defaults (`claude-sonnet-5-5`, `thinking = "between_tools"`, `tool_choice` auto, no `temperature`; see Request parameters in `docs/configuration.md`). This has not been run live yet.

## Helpers (`tests/helpers/`)

Import them with `from helpers import ...`. pytest's rootdir insertion makes `tests/` importable; there is no `sys.path` manipulation.

| Helper | Purpose |
|---|---|
| `make_config(tmp_path, **sections)` | A `Config` with `log.dir` and `stub.state_file` under `tmp_path`, `stub.time_scale = 0.01`, and base dir = repo root (so `skills/` loads). Overrides are section dicts, for example `make_config(tmp_path, loop={"max_failures": 1})`. |
| `ScriptedPlanner(items, on_call=None)` | Implements `PlannerClient`. Items are returned in order: a `Plan` → a valid `LLMResult`; a `dict` → raw tool input, validated like a real reply; an exception instance → raised. Records every call's kwargs in `.calls`. `on_call(call_index)` runs before it returns (for example to set the stop event during a call). Raises `AssertionError` if called more often than scripted. Usage is `{"input_tokens": 100, "output_tokens": 20}`. |
| `FakeClock(start=1000.0)` | `now()`, `advance(seconds)`. |
| `FakeExecutor(results, *, stop_move_ok=True, stop_move_posture=None, on_kill=None)` | Returns scripted `ExecResult`s. An item can also be an exception (raised) or a callable `f(call_kwargs) -> ExecResult` (to block, set the stop event, or advance the clock). Records `runs`, `kills`, `stop_moves`. `exec_result(...)` and `stop_move_result(...)` build results. |
| `fake_update`, `fake_context`, `replies` | Minimal Telegram `Update` / `Context` stand-ins with an `AsyncMock` `reply_text`. |
| `stub_env`, `run_module`, `single_response` | Run a `go2_skills` module in a subprocess with a stub environment and parse its single response line. |
| `planner_factory.factory()` | Builds a `ScriptedPlanner` from env `GO2_TEST_SCRIPT` (JSON list of raw tool inputs). Used by CLI subprocess tests through `GO2_TEST_PLANNER=planner_factory:factory` with `tests/helpers` on `PYTHONPATH`. |
| `skill_modules/` | Small modules used as skill entrypoints in registry and executor tests (for example `env_dump`, which reports its environment keys). |

The test planner hook also works by hand, for a stub run with no API key:

```bash
GO2_TEST_PLANNER=planner_factory:factory PYTHONPATH=tests/helpers \
GO2_TEST_SCRIPT='[{"status":"PLAN","steps":[{"skill":"turn","params":{"direction":"left"}}]},{"status":"DONE","steps":[],"message":"Turned."}]' \
uv run go2-dispatch run "turn left"
```

## Fault injection in tests

The stub fault kinds `error`, `hang`, `crash` and `garbage` (`docs/running.md`) drive the executor and end-to-end tests through `stub.faults` (by dispatched step) or the `GO2_STUB_FAULT` env var (one process). `GO2_STUB_NOISE=1` makes the stub write junk to stdout, to check that the response line stays clean.

## Golden files

`tests/golden/catalog.txt` is the exact catalog for the five skills (no trailing newline). The `context_*.txt` files are exact user messages: first call, after a checkpoint, after a failure, after a rejection, with a previous task, and a schema retry. Any change to the wording in `prompts.py`, the renderer, or a `SKILL.md` changes them. Run `uv run pytest --update-golden`, review the diff, and remember that changing the prompt surface changes the registry hash and makes runs incomparable across the change (OD-9).

## Robot checklist (supervised, manual)

Run this on the lab machine with `robot.backend = "real"`, **in this order**, with the robot **standing**, the area clear, and the remote e-stop in hand. Read `docs/safety.md` first. Record every result in `docs/decisions.md` (date, value measured, what was decided).

Commands use `go2-dispatch` with a real-backend config. Steps are run as one-step tasks, or with the skill by hand (`docs/skills.md`), whichever is easier to observe.

1. `go2-dispatch state` returns within 1 s. Record `mode`, `body_height` and `position` while standing.
2. `walk` forward 0.5 m; `turn` left 90°. Check the angle visually.
3. `stretch` from standing. Measure how long the routine takes (this tunes `StretchPolicy.SETTLE_S`; OD-10).
4. `detect_object person` with a person in view.
5. Operator `stop` during a 3 m walk. **Measure** the kill-to-stop latency (`stop_move.timing.stop_call_ms` from the run log, plus the observed time). Record it; there is no pass threshold.
6. Step timeout during a walk: temporarily set `WalkPolicy.BASE_S = 0` and `FACTOR = 0.5` in `src/go2_skills/walk.py`. The robot must stop, and the outcome must be `timeout`. Restore the values afterwards.
7. `sit`. Measure how long StandDown takes (this tunes `SitPolicy.SETTLE_S`). Then `go2-dispatch state`: record `mode` and `body_height` while sitting (this resolves OD-2).
8. `walk` while down. Record the actual SDK return code and behaviour (this resolves OD-4).
9. Stand the robot up with the remote (there is no `stand` skill; OD-1).

| # | Date | Result / value | Decision |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |

(Use this table as a worksheet. The record of each result goes in `docs/decisions.md`.)
