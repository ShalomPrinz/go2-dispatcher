# Testing

Running the test suite: layout, markers and opt-in flags, helpers and fakes, golden files. The manual robot checks are in [robot.md](../skills/docs/robot.md#supervised-robot-checklist).

## Running the tests

```bash
uv run pytest                    # everything that needs no network, API key, robot or SDK
uv run pytest -q dispatcher/tests/unit skills/tests/unit  # unit tests only
uv run pytest -m integration     # real subprocesses on the stub backend
uv run pytest -k context         # by name
```

### Coverage

```bash
uv run pytest --cov              # branch coverage of dispatcher and skills, with missing lines
```

Coverage is opt-in (`pytest-cov`; settings in `[tool.coverage.*]` in `pyproject.toml`). It measures branches, and it also measures the skill, utility and CLI subprocesses the integration tests start (coverage's `[run] patch = ["subprocess", "_exit"]`; `_exit` is needed because skills end with `os._exit`). Processes killed with SIGKILL (timeouts, stops) record nothing. A covered run takes about 20 s instead of about 12 s.

The default run must pass on any machine after `uv sync` (core + dev dependencies only). Every test has a 30 s timeout (`pytest-timeout`). Parallel runs are opt-in (see design decisions below).

## Layout

| Folder | What |
|---|---|
| `dispatcher/tests/unit/` | config, process lock, registry, tool schema, plan validation, bounds, precheck, motion budget, render + context, transport start-up (planner choice, initial posture), prompts (fixed-texts golden file), LLM client (mock HTTP), dispatcher loop, `format_outcome`, Telegram handlers |
| `dispatcher/tests/integration/` | executor, end-to-end with the real executor, CLI subprocesses (including "the CLI does not import `anthropic`"), live LLM |
| `dispatcher/tests/golden/` | expected catalog, context and fixed texts |
| `dispatcher/tests/helpers/` | shared helpers (below) |
| `skills/tests/unit/` | skill policies (timeout covers the motion), shared skill helpers (`parse_params`, `require_*`, backend selection), motion loop, single actions and the `stop_move` utility with a fake sport client, real-robot state mapping (`real._state_from_msg`), skill response and executor response parsing, posture |
| `skills/tests/integration/` | per-skill contract (one subprocess per skill), stub behaviour, one subprocess case per shared mechanism (invalid params, backend not configured, noise, faults), utilities, orphan watchdog, side-effect-free imports |
| `skills/tests/robot/` | reserved for opt-in robot tests; empty in v1 (the robot checks are manual) |

Each test lives in the service whose code it exercises. `test_skill_response.py` sits under `skills/` because it mostly checks `skills.result.build_response`; it also checks that the dispatcher's models and response parsing accept that output.

## Markers and flags

| Marker | Meaning | Enabled by |
|---|---|---|
| `integration` | spawns real subprocesses with the stub backend | always runs |
| `live_llm` | calls the real Anthropic API | `--run-live` and `ANTHROPIC_API_KEY` |
| `robot` | needs the real Go2 | `--run-robot` |

Options (defined in the root `conftest.py`):

- `--run-live`: run `live_llm` tests. They are skipped without it, and also skipped if `ANTHROPIC_API_KEY` is not set.
- `--run-robot`: run `robot` tests. There are none in v1; the robot checks are the supervised checklist in [robot.md](../skills/docs/robot.md#supervised-robot-checklist).
- `--update-golden`: rewrite the golden files instead of comparing against them. Review the diff with `git diff dispatcher/tests/golden` before committing.

### Live LLM test

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest --run-live -s dispatcher/tests/integration/test_live_llm.py
```

It runs the task "turn left 90 degrees, then tell me if you see a chair" on the stub with `stub.detections = {chair = "center:near"}`. It passes if the task ends `DONE`, the message mentions the chair, and the run log has no `plan_invalid` or `horizon_rejection` record. With `-s`, it prints the run log path.

It uses the default LLM settings (`claude-sonnet-5-5`, `thinking = "between_tools"`, `tool_choice` auto, no `temperature`; see [llm.md](../dispatcher/docs/llm.md#the-request)). **It has not been run live yet**; running it is the pending check of the request parameters ([roadmap.md](roadmap.md#pending-human-work)).

## Helpers (`dispatcher/tests/helpers/`)

Import them with `from helpers import ...`, from either service's tests. `pythonpath = ["dispatcher/tests"]` in `[tool.pytest.ini_options]` makes them importable; there is no `sys.path` manipulation in the tests. Nothing on that path may shadow the `dispatcher` or `skills` packages (hence `helpers/skill_process.py`, not `skills.py`).

| Helper | Purpose |
|---|---|
| `make_config(tmp_path, **sections)` | A `Config` with `log.dir` and `stub.state_file` under `tmp_path`, `stub.time_scale = 0.01`, and base dir = repo root (so `skills/catalog/` loads). Overrides are section dicts, for example `make_config(tmp_path, loop={"max_failures": 1})`. |
| `ScriptedPlanner(items, on_call=None)` | Implements `PlannerClient`. Items are returned in order: a `Plan` → a valid `LLMResult`; a `dict` → raw tool input, validated like a real reply; an exception instance → raised. Records every call's kwargs in `.calls`. `on_call(call_index)` runs before it returns (for example to set the stop event during a call). Raises `AssertionError` if called more often than scripted. Usage is `{"input_tokens": 100, "output_tokens": 20}`. |
| `FakeClock(start=1000.0)` | `now()`, `advance(seconds)`. |
| `FakeExecutor(results, *, stop_move_ok=True, stop_move_posture=None, on_kill=None)` | Returns scripted `ExecResult`s. An item can also be an exception (raised) or a callable `f(call_kwargs) -> ExecResult` (to block, set the stop event, or advance the clock). Records `runs`, `kills`, `stop_moves`. `exec_result(...)` and `stop_move_result(...)` build results. |
| `fake_update`, `fake_context`, `replies` | Minimal Telegram `Update` / `Context` stand-ins with an `AsyncMock` `reply_text`. |
| `stub_env`, `run_module`, `single_response` | Run a `skills` module in a subprocess with a stub environment and parse its single response line. |
| `planner_factory.factory()` | Builds a `ScriptedPlanner` from env `GO2_TEST_SCRIPT` (JSON list of raw tool inputs). Used by CLI subprocess tests through `GO2_TEST_PLANNER=planner_factory:factory` with `dispatcher/tests/helpers` on `PYTHONPATH`. |
| `skill_modules/` | Small modules used as skill entrypoints in registry and executor tests (for example `env_dump`, which reports its environment keys). |

The test planner hook also works by hand, for a stub run with no API key:

```bash
GO2_TEST_PLANNER=planner_factory:factory PYTHONPATH=dispatcher/tests/helpers \
GO2_TEST_SCRIPT='[{"status":"PLAN","steps":[{"skill":"turn","params":{"direction":"left"}}]},{"status":"DONE","steps":[],"message":"Turned."}]' \
uv run go2 run "turn left"
```

## Fault injection in tests

The stub fault kinds `error`, `hang`, `crash` and `garbage` ([skills.md](../skills/docs/skills.md#fault-injection)) drive the executor and end-to-end tests through `stub.faults` (by dispatched step) or the `GO2_STUB_FAULT` env var (one process). `GO2_STUB_NOISE=1` makes the stub write junk to stdout, to check that the response line stays clean.

## Golden files

`dispatcher/tests/golden/catalog.txt` is the exact catalog for the five skills (no trailing newline). The `context_*.txt` files are exact user messages: first call, after a checkpoint, after a failure, after a rejection, with a previous task, and a schema retry. `fixed_texts.txt` renders every fixed text in `prompts.py` with example arguments, one labelled section each: every operator message with and without the StopMove warning, every notice, the motion-budget message, the rejection section, the transport texts, `help_text("stub")` and both system blocks for horizon 5. Any change to the wording in `prompts.py`, the renderer, or a `SKILL.md` changes them. Run `uv run pytest --update-golden`, review the diff, and remember that changing the prompt surface changes the registry hash and makes runs incomparable across the change ([skills.md](../skills/docs/skills.md#catalog-and-registry-hash)). The fixed texts must stay identical across experimental conditions ([loop-and-context.md](../dispatcher/docs/loop-and-context.md)).

## Robot checks

There are no automated robot tests in v1. The real backend is checked by hand with the supervised checklist in [robot.md](../skills/docs/robot.md#supervised-robot-checklist), where its results are also recorded.

## Writing tests

- The default run must stay offline: no network, API key, SDK or robot. Mark anything else `live_llm` or `robot`.
- Keep the suite fast. Most of its size comes from parameterised cases; prefer fewer, meaningful tests over more parameter combinations.
- Use the fakes above for dispatcher logic and the stub with real subprocesses (`integration`) for anything that depends on processes, timeouts or kills.
- Cut cases only for redundancy (same code path, same input class) or cost (one interpreter start per subprocess case), never to lower the count.
- Keep parametrisation and shrink the input set; never fold cases into a loop inside one test, which stops at the first failure. Instead of a cross product (fields × values), write two parametrised tests: all bad values on one field, and every field with one bad value.
- For thresholds and ranges, test just below, at and above each boundary, plus `None`; drop interior points.
- Test shared skill code (`parse_params`, `require_*`, `capture_stdout`, stub faults, the orphan watchdog) once, in process where possible, plus one subprocess case per mechanism. Per-skill fan-out is only for per-skill code: `test_contract_valid` runs every skill, because each skill is its own entrypoint.
- Do not re-type fixed texts or constants in assertions; fixed texts are checked through `fixed_texts.txt`. Do not test pydantic, Python, or the test helpers themselves.

## Design decisions

- **SDK-calling skill code is tested in process with a fake sport client.** The stub's `error` fault fails only the first SDK call, so mid-loop failures, a failing `StopMove`, an exception during `Move` and the orphan break in `motion.move_loop`, and the failure branches of `stop_move.main()`, are reached only by monkeypatching `backend.get_sport_client`, `backend.sleep`, `backend.sample_state` and `result.emit` (which would otherwise exit the process). Adding faults to the stub for these paths was rejected: it would grow the stub for test-only behaviour and still cost one interpreter start per case.
- **Coverage is opt-in, not in `addopts`.** Measuring subprocesses makes the run about 60 % slower, and the default run must stay fast.
