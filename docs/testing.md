# Testing

Running the test suite: layout, markers and opt-in flags, helpers and fakes, golden files. The manual robot checks are in [robot.md](../skills/docs/robot.md#supervised-robot-checklist).

## Running the tests

```bash
uv run pytest                    # everything that needs no network, API key, robot or SDK
uv run pytest -q dispatcher/tests skills/tests  # unit tests of both services only
uv run pytest -q tests           # cross-service tests only
uv run pytest -m integration     # real subprocesses on the stub backend
uv run pytest -k context         # by name
```

### Lint

```bash
uv run ruff check .              # lint (settings in [tool.ruff] in pyproject.toml)
uv run ruff check --fix .        # apply safe fixes
```

The lint must pass before a commit; CI runs it next to the default suite. Rules: pycodestyle, pyflakes, isort, bugbear and pyupgrade (`E`, `F`, `W`, `I`, `B`, `UP`) for Python 3.10, line length 120.

### Coverage

```bash
uv run pytest --cov              # branch coverage of dispatcher and skills, with missing lines
```

Coverage is opt-in (`pytest-cov`; settings in `[tool.coverage.*]` in `pyproject.toml`). It measures branches, and it also measures the skill, utility and CLI subprocesses the integration tests start (coverage's `[run] patch = ["subprocess", "_exit"]`; `_exit` is needed because skills end with `os._exit`). Processes killed with SIGKILL (timeouts, stops) record nothing. A covered run takes about 20 s instead of about 12 s.

The default run must pass on any machine after `uv sync` (core + dev dependencies only). CI (`.github/workflows/ci.yml`) runs it on every push to any branch, on Python 3.10 and 3.11, after `uv sync --locked`. Every test has a 30 s timeout (`pytest-timeout`). Parallel runs are opt-in (see design decisions below).

## Layout

| Folder | What |
|---|---|
| `dispatcher/tests/unit/` | config, process lock, registry, tool schema, plan validation, bounds, precheck, motion budget, render + context, transport start-up (planner choice, initial posture), prompts (fixed-texts golden file), LLM client (mock HTTP), dispatcher loop, `format_outcome`, Telegram handlers, executor response parsing (`test_parse_response.py`) |
| `dispatcher/tests/golden/` | expected catalog, context and fixed texts |
| `dispatcher/tests/helpers/` | dispatcher-only helpers (below) |
| `skills/tests/unit/` | shared skill helpers (`parse_params`, `require_*`, backend selection), motion loop, single actions and the `stop_move` utility with a fake sport client, real-robot state mapping (`real._state_from_msg`), `build_response` argument checks and message cutting, posture |
| `skills/tests/robot/` | reserved for opt-in robot tests; empty in v1 (the robot checks are manual) |
| `tests/integration/` | cross-service tests: executor, end-to-end with the real executor, CLI subprocesses (including "the CLI does not import `anthropic`"), live LLM, per-skill contract (one subprocess per skill), stub behaviour, one subprocess case per shared mechanism (invalid params, backend not configured, noise, faults), utilities, orphan watchdog, side-effect-free imports, skill policies against the registry (timeout covers the motion), `build_response` output against `SkillResponse` |
| `tests/helpers/` | helpers shared by the root tests and a service suite (below) |
| `tests/pytest_plugin.py` | the `--run-live`, `--run-robot` and `--update-golden` options, marker gating and the `update_golden` fixture |

A service folder holds unit tests of that service only: `skills/tests/` never imports `dispatcher`, while `dispatcher/tests/` may import `skills` (the dispatcher depends on skills). A test that needs both services, or that runs the services as real subprocesses, lives in the root `tests/` (see design decisions below).

## Markers and flags

| Marker | Meaning | Enabled by |
|---|---|---|
| `integration` | spawns real subprocesses with the stub backend | always runs |
| `live_llm` | calls the real Anthropic API | `--run-live` and `ANTHROPIC_API_KEY` |
| `robot` | needs the real Go2 | `--run-robot` |

Options (defined in the pytest plugin `tests/pytest_plugin.py`, loaded by `addopts = "-p tests.pytest_plugin"` in `pyproject.toml`, so they are available whichever folder is run):

- `--run-live`: run `live_llm` tests. They are skipped without it, and also skipped if `ANTHROPIC_API_KEY` is not set.
- `--run-robot`: run `robot` tests. There are none in v1; the robot checks are the supervised checklist in [robot.md](../skills/docs/robot.md#supervised-robot-checklist).
- `--update-golden`: rewrite the golden files instead of comparing against them. Review the diff with `git diff dispatcher/tests/golden` before committing.

### Live LLM test

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest --run-live -s tests/integration/test_live_llm.py
```

It runs the task "turn left 90 degrees, then tell me if you see a chair" on the stub with `stub.detections = {chair = "center:near"}`. It passes if the task ends `DONE`, the message mentions the chair, and the run log has no `plan_invalid` or `horizon_rejection` record. With `-s`, it prints the run log path.

It uses the default LLM settings (`claude-sonnet-5-5`, `thinking = "between_tools"`, `tool_choice` auto, no `temperature`; see [llm.md](../dispatcher/docs/llm.md#the-request)). **It has not been run live yet**; running it is the pending check of the request parameters ([roadmap.md](roadmap.md#pending-human-work)).

## Helpers

Import them with absolute imports: `from tests.helpers import ...` for shared helpers (`tests/helpers/`) and `from dispatcher.tests.helpers import ...` for dispatcher-only ones (`dispatcher/tests/helpers/`); root tests may import both. `tests`, `dispatcher.tests` and `skills.tests` are packages (each test folder has an `__init__.py`), and the editable install from `uv sync` puts the repo root on `sys.path`, so there is no `pythonpath` setting and no `sys.path` manipulation in the tests. `skills/tests/` imports nothing from `dispatcher`.

Shared (`tests/helpers/`): `REPO_ROOT`, `TEST_TIME_SCALE`, `stub_env`, `run_module`, `single_response`. Dispatcher-only (`dispatcher/tests/helpers/`): everything else in the table.

| Helper | Purpose |
|---|---|
| `make_config(tmp_path, **sections)` | A `Config` with `log.dir` and `stub.state_file` under `tmp_path`, `stub.time_scale = 0.01`, and base dir = repo root (so `skills/catalog/` loads). Overrides are section dicts, for example `make_config(tmp_path, loop={"max_failures": 1})`. |
| `ScriptedPlanner(items, on_call=None)` | Implements `PlannerClient`. Items are returned in order: a `Plan` → a valid `LLMResult`; a `dict` → raw tool input, validated like a real reply; an exception instance → raised. Records every call's kwargs in `.calls`. `on_call(call_index)` runs before it returns (for example to set the stop event during a call). Raises `AssertionError` if called more often than scripted. Usage is `{"input_tokens": 100, "output_tokens": 20}`. |
| `FakeClock(start=1000.0)` | `now()`, `advance(seconds)`. |
| `FakeExecutor(results, *, stop_move_ok=True, stop_move_posture=None, on_kill=None)` | Returns scripted `ExecResult`s. An item can also be an exception (raised) or a callable `f(call_kwargs) -> ExecResult` (to block, set the stop event, or advance the clock). Records `runs`, `kills`, `stop_moves`. `exec_result(...)` and `stop_move_result(...)` build results. |
| `fake_update`, `fake_context`, `replies` | Minimal Telegram `Update` / `Context` stand-ins with an `AsyncMock` `reply_text`. |
| `stub_env`, `run_module`, `single_response` | Run a `skills` module in a subprocess with a stub environment and parse its single response line. |
| `planner_factory.factory()` | Builds a `ScriptedPlanner` from env `GO2_TEST_SCRIPT` (JSON list of raw tool inputs). Used by CLI subprocess tests through `GO2_TEST_PLANNER=planner_factory:factory` with `dispatcher/tests/helpers` on `PYTHONPATH` (a top-level import, so the subprocess does not load the whole helpers package). |
| `skill_modules/` | Small modules used as skill entrypoints in registry tests (`dispatcher.tests.helpers.skill_modules.<name>`) and the executor test (`env_dump`, which reports its environment keys; run as `skill_modules.env_dump` with `dispatcher/tests/helpers` on `PYTHONPATH`). |

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
- Remove a case only if it is redundant (same code path and input class as another case) or costly (each subprocess case starts an interpreter). The case count is not a goal.
- Keep cases parametrised so each failure reports by name; never loop over inputs inside one test. Instead of a cross product (fields × values), write two parametrised tests: every bad value on one field, and every field with one bad value.
- For thresholds and ranges, test just below, at and above each boundary, plus `None`.
- Fan out per skill only for per-skill code (`test_contract_valid`, because each skill is its own entrypoint). Test shared skill code once, in process, plus one subprocess case per mechanism.
- Do not re-type fixed texts or constants in assertions; fixed texts are checked through `fixed_texts.txt`. Do not test pydantic, Python, or the test helpers themselves.

## Design decisions

- **SDK-calling skill code is tested in process with a fake sport client.** The stub's `error` fault fails only the first SDK call, so mid-loop failures, a failing `StopMove`, an exception during `Move` and the orphan break in `motion.move_loop`, and the failure branches of `stop_move.main()`, are reached only by monkeypatching `backend.get_sport_client`, `backend.sleep`, `backend.sample_state` and `result.emit` (which would otherwise exit the process). Adding faults to the stub for these paths was rejected: it would grow the stub for test-only behaviour and still cost one interpreter start per case.
- **Service folders hold unit tests of their own service; cross-service tests live in a root `tests/` package.** `skills/tests/` must not import `dispatcher`, so that the dependency direction of the code (the dispatcher imports skills, never the reverse) also holds for the tests. Integration tests drive both services (the executor starts skill processes, skill responses are parsed by the dispatcher's models), so they are cross-service by nature and all live in `tests/integration/`, together with the in-process tests that check skills against the dispatcher's registry or models. Rejected: keeping each test in the service whose code it mostly exercises, which made `skills/tests/` import `dispatcher`.
- **Pytest options come from a plugin module (`-p tests.pytest_plugin`), not a root `conftest.py`.** pytest registers an option only once, so the options cannot be defined in each suite's conftest, and a conftest is loaded only for paths below it, so a conftest in one suite is not loaded when another suite is run on its own. A root `conftest.py` worked but sat outside every test folder; the plugin keeps all test code under `tests/` and makes the options available for any subset (`uv run pytest skills/tests`). Helpers are imported with absolute package paths instead of a `pythonpath` entry, which avoids a bare top-level `helpers` module that could shadow other names.
- **Coverage is opt-in, not in `addopts`.** Measuring subprocesses makes the run about 60 % slower, and the default run must stay fast.
- **Ruff lints; it does not format.** The code uses hand-aligned trailing comments and long fixed-text lines that the formatter would rewrap (68 files). Line length is 120, not ruff's 88, which matches the existing code. `dispatcher/prompts.py` is exempt from `E501` because rewrapping its fixed texts would change the registry hash. Rejected: `ruff format`, for the churn and the alignment loss.
- **CI runs only the default suite and the lint.** Live LLM tests would put the API key into CI secrets and cost money per push, and robot tests need a supervised session ([safety.md](safety.md)); both stay opt-in and manual. The `robot` and `vision` extras are not installed in CI, which also checks that the default run needs none of them.
