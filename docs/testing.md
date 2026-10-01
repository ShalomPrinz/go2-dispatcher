# Testing

Running the test suite: layout, markers and opt-in flags, helpers and fakes, golden files. The manual robot checks are in [robot.md](robot.md#supervised-robot-checklist).

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
| `tests/robot/` | reserved for opt-in robot tests; empty in v1 (the robot checks are manual) |

## Markers and flags

| Marker | Meaning | Enabled by |
|---|---|---|
| `integration` | spawns real subprocesses with the stub backend | always runs |
| `live_llm` | calls the real Anthropic API | `--run-live` and `ANTHROPIC_API_KEY` |
| `robot` | needs the real Go2 | `--run-robot` |

Options (defined in `tests/conftest.py`):

- `--run-live`: run `live_llm` tests. They are skipped without it, and also skipped if `ANTHROPIC_API_KEY` is not set.
- `--run-robot`: run `robot` tests. There are none in v1; the robot checks are the supervised checklist in [robot.md](robot.md#supervised-robot-checklist).
- `--update-golden`: rewrite the golden files instead of comparing against them. Review the diff with `git diff tests/golden` before committing.

### Live LLM test

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest --run-live -s tests/integration/test_live_llm.py
```

It runs the task "turn left 90 degrees, then tell me if you see a chair" on the stub with `stub.detections = {chair = "center:near"}`. It passes if the task ends `DONE`, the message mentions the chair, and the run log has no `plan_invalid` or `horizon_rejection` record. With `-s`, it prints the run log path.

It uses the default LLM settings (`claude-sonnet-5-5`, `thinking = "between_tools"`, `tool_choice` auto, no `temperature`; see [llm.md](llm.md#the-request)). **It has not been run live yet**; running it is the pending check of the request parameters ([roadmap.md](roadmap.md#pending-human-work)).

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

The stub fault kinds `error`, `hang`, `crash` and `garbage` ([skills.md](skills.md#fault-injection)) drive the executor and end-to-end tests through `stub.faults` (by dispatched step) or the `GO2_STUB_FAULT` env var (one process). `GO2_STUB_NOISE=1` makes the stub write junk to stdout, to check that the response line stays clean.

## Golden files

`tests/golden/catalog.txt` is the exact catalog for the five skills (no trailing newline). The `context_*.txt` files are exact user messages: first call, after a checkpoint, after a failure, after a rejection, with a previous task, and a schema retry. Any change to the wording in `prompts.py`, the renderer, or a `SKILL.md` changes them. Run `uv run pytest --update-golden`, review the diff, and remember that changing the prompt surface changes the registry hash and makes runs incomparable across the change ([skills.md](skills.md#catalog-and-registry-hash)). The fixed texts must stay identical across experimental conditions ([loop-and-context.md](loop-and-context.md)).

## Robot checks

There are no automated robot tests in v1. The real backend is checked by hand with the supervised checklist in [robot.md](robot.md#supervised-robot-checklist), where its results are also recorded.

## Writing tests

- The default run must stay offline: no network, API key, SDK or robot. Mark anything else `live_llm` or `robot`.
- Keep the suite fast. Most of its size comes from parameterised cases; prefer fewer, meaningful tests over more parameter combinations.
- Use the fakes above for dispatcher logic and the stub with real subprocesses (`integration`) for anything that depends on processes, timeouts or kills.
