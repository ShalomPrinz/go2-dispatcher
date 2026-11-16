# Testing

Running the test suite: layout, markers and opt-in flags, helpers and fakes, golden files. The manual robot checks are in [robot.md](../../services/robot/docs/robot.md#supervised-robot-checklist).

## Running the tests

```bash
uv run pytest                    # everything that needs no network, API key, robot or SDK (includes the hook tests)
uv run pytest -q dispatcher/tests skills/tests contract/tests services/robot/tests services/vision/tests services/backend/tests analysis/tests  # unit tests of the packages only
uv run pytest -q tests           # cross-service tests only
uv run pytest -m integration     # real subprocesses on the stub backend
uv run pytest -k context         # by name
```

### Lint and format

```bash
uv run ruff check .              # lint (settings in [tool.ruff] in pyproject.toml)
uv run ruff check --fix .        # apply safe fixes
uv run ruff format .             # format (ruff defaults, line length 120)
```

Formatting must be clean and the lint must pass before a commit; both run locally, not in CI. A Claude Code hook also formats and lints the changed files at the end of each turn ([hooks README](../../.claude/hooks/README.md)). `.git-blame-ignore-revs` lists the formatting sweep (`git config blame.ignoreRevsFile .git-blame-ignore-revs`). Rules: pycodestyle, pyflakes, isort, bugbear and pyupgrade (`E`, `F`, `W`, `I`, `B`, `UP`) for Python 3.10, line length 120.

### Type check

```bash
uv run basedpyright              # type check (settings in [tool.basedpyright] in pyproject.toml)
uv run basedpyright <files>      # changed files only; no `--` separator, it is read as a path
```

basedpyright in `basic` mode for Python 3.10 checks `dispatcher`, `skills`, `contract`, `services`, `analysis` and `tests`, and must pass on the changed files before a commit. It needs the optional extras installed (`uv sync --extra robot --extra vision`); without them the SDK and detector imports in `services/robot/backends/real.py` and `services/vision/detectors/real.py` are reported as unresolved. It passes on the whole repo. A Claude Code hook also type-checks the changed files at the end of each turn ([hooks README](../../.claude/hooks/README.md)).

### Import boundaries

```bash
uv run lint-imports              # import contracts (settings in [tool.importlinter] in pyproject.toml)
```

`import-linter` checks twenty-two contracts over the import graph of `skills`, `services`, `dispatcher`, `contract`, `analysis` and the root `tests` package, following indirect imports through the repo's own modules (third-party packages are leaf nodes, not analysed internally), and must pass before a commit; a Claude Code hook runs it at the end of each turn in which a `.py` file changed ([hooks README](../../.claude/hooks/README.md)), and CI runs it before the tests. Every contract names packages, not skill modules, so a new skill module is covered without a change to `pyproject.toml`; the modules under each package's `tests/` folder are checked with it.

Another package imports `services.robot` and `services.vision` only through their client surfaces: `services.robot.protocol`, `services.robot.client`, `services.robot.ramp` (and `services.robot.state_store`, which `services.robot.protocol` imports) and `services.vision.protocol` and `services.vision.client` ([service.md](../../services/robot/docs/service.md#the-package), [vision.md](../../services/vision/docs/vision.md#the-package)). The dispatcher's surface also holds `services.robot.stub`, the stub reset, which imports `services.backend.stub_state`; skills may import neither. `services.backend`, the code both services share for the backend they run on ([backend.md](../../services/backend/docs/backend.md)), is imported only by `services.robot` and `services.vision`. The contracts list the modules outside the surfaces (`services.robot.service`, `services.robot.backends`, `services.robot.lease`, `services.robot.stop_move`, `services.robot.read_state`, `services.vision.service`, `services.vision.detectors`, `services.vision.boxes`), so a new module of `services.robot` or `services.vision` that is not part of a surface is added to both lists. The utilities `services.robot.stop_move` and `services.robot.read_state` are outside the surfaces: the dispatcher runs them as subprocesses by module name, which it reads with their policy from the descriptors `STOP_MOVE` and `READ_STATE` in `services.robot.protocol`, and never imports them.

| Contract | Rule |
|---|---|
| Skills import only the client surfaces of robot and vision, and no heavy or dispatcher-side package | No `skills` module imports `dispatcher`, `analysis`, `pydantic`, `yaml`, `unitree_sdk2py`, `cyclonedds`, `torch`, `ultralytics`, `cv2`, or a `robot` or `vision` module outside the client surfaces, nor `services.robot.stub` or `services.backend` (the rule and its reasons: [skills.md](../../skills/docs/skills.md#no-side-effects-on-import)); this also holds through `contract`, `robot` and `vision`, whose modules a skill imports. A skill process therefore never touches DDS and never loads the detector. |
| The robot and vision services are independent of each other | `services.robot` and `services.vision` import each other neither directly nor through a chain (`independence`); what both need (the stub state file, the DDS init) is in `services.backend`. |
| The shared backend package imports neither service, skills nor the dispatcher | `services.backend` imports none of `services.robot`, `services.vision`, `skills` and `dispatcher`, so it stays below both services and loading it loads no service, backend or SDK. |
| The real backend loads no sim or fault code | `services.robot.backends.real` imports none of `services.robot.backends.sim`, `services.robot.backends.faults` and `contract.faults`: a real robot process never loads fault injection ([service.md](../../services/robot/docs/service.md#fault-injection), [safety.md](../../docs/safety.md#supervised-operation-rules)). Direct imports only (`allow_indirect_imports`); see the runtime check below. |
| The robot service gets faults only through the backend object | `services.robot.service` imports neither `services.robot.backends.faults` nor `contract.faults`; the sim backend hands it the fault-injecting sport client and arm hook. Direct imports only, as above. |
| Only the sim backend and robot tests import the robot fault code | Only `services.robot.backends.sim`, `services.robot.tests` and `tests.integration` import `services.robot.backends.faults` directly (`protected`). |
| The services import neither skills, the dispatcher nor analysis | `services` is below `skills`, `dispatcher` and `analysis`: no `services` module imports any of them. |
| The dispatcher imports no skill | No `dispatcher` module (tests included) imports `skills`: the registry loads each skill by its `entrypoint` name at run time, which import-linter does not see, so a static import would be a hidden dependency. A test skill that needs `skills.runtime` lives outside `dispatcher`, as `tests/integration/env_dump.py` does. |
| The dispatcher imports only the client surfaces of robot and vision | No `dispatcher` module imports a `robot` or `vision` module outside the client surfaces: it starts the services as subprocesses by module name (`SERVICE_MODULE` in each `protocol`), names no backend module and never loads a detector. It runs the utilities `stop_move` and `read_state` the same way, by the module names in `services.robot.protocol`, so no backend module is loaded into the dispatcher process. |
| The dispatcher reaches the stub world only through the robot's stub control | No `dispatcher` module imports `services.backend` directly; it resets the stub through `services.robot.stub.reset_stub` ([backend.md](../../services/backend/docs/backend.md#design-decisions)). Indirect imports are allowed (`allow_indirect_imports`), because `services.robot.stub` itself imports `services.backend.stub_state`. |
| Only the composition root, execution and the registry import the services | None of `dispatcher.dispatcher`, `planning`, `llm`, `context`, `runlog`, `transports`, `config`, `models`, `prompts`, `operator_messages` and `process_lock` imports `services` directly; `dispatcher.app` (the composition root), `dispatcher.execution` and `dispatcher.registry` (which checks the `uses_vision` declaration through `services.vision.client`) do, and the transports reach the services only through `dispatcher.app`. Indirect imports are allowed (`allow_indirect_imports`), because the transports import `dispatcher.app`, which imports `dispatcher.execution`. Dispatcher tests are not sources. A new top-level dispatcher module is added to the source list. |
| The run log imports no loop, execution, transport or composition root | No `dispatcher.runlog` module imports `dispatcher.dispatcher`, `dispatcher.execution`, `dispatcher.transports` or `dispatcher.app`; the loop writes to the run log, never the reverse. Its type-checking imports (`dispatcher.models`, `llm`, `planning`) count as imports. |
| Planning and llm are independent of each other | `dispatcher.planning` and `dispatcher.llm` import each other neither directly nor through any chain (`independence`). |
| Planning imports no run-time, provider or transport package | No `dispatcher.planning` module imports `dispatcher.context`, `runlog`, `execution`, `robot`, `vision`, `transports`, `dispatcher.dispatcher`, `anthropic` or `httpx`; it uses `contract`, `dispatcher.registry`, `dispatcher.prompts` and `dispatcher.models`. `dispatcher.llm` is covered by the independence contract. |
| Llm imports no planning, context, registry, run-time or transport module | No `dispatcher.llm` module imports `dispatcher.context`, `registry`, `runlog`, `execution`, `robot`, `vision`, `transports` or `dispatcher.dispatcher`; it uses `dispatcher.config`, `dispatcher.models` and the lazy SDKs. `dispatcher.planning` is covered by the independence contract. |
| Only the vision service imports the detector stack | Only `services.vision.service`, `services.vision.detectors` and `services.vision.tests` import `services.vision.detectors`, `services.vision.service`, `ultralytics` or `cv2` directly (`protected`), so no other process loads the detector ([vision.md](../../services/vision/docs/vision.md#vision-service)). `torch` is not listed because no module imports it by name; `ultralytics` loads it. |
| The analysis package reads logs only | No `analysis` module imports `dispatcher.registry`, `execution`, `app`, `dispatcher.dispatcher`, `planning`, `llm`, `context`, `transports`, `skills` or `services`; it uses `dispatcher.runlog` (the file-format constants) and `dispatcher.config` (`Provider`). Indirect imports count too. The contract holds because `analysis` imports only `dispatcher.runlog.format`, whose sole internal chain is `dispatcher.config` -> `dispatcher.models` -> `contract`; the other `dispatcher.runlog` modules reach `dispatcher.llm` and `dispatcher.planning` through type-checking imports, so importing any of them from `analysis` breaks the contract. |
| Only the CLI imports analysis | Only `dispatcher.transports.cli` and the `tests` package import `analysis` (`protected`, direct imports). |
| Shared test helpers import no project package | `tests.helpers` imports none of the six packages, so a package's unit tests that import a shared helper depend on no other package. |
| Contract imports no other project package | `contract` imports none of `skills`, `dispatcher`, `robot`, `vision`, `analysis` and `tests` (`tests` so that no chain through `tests.helpers` hides one). |
| Contract imports neither pydantic nor yaml, except the SKILL.md parser | No `contract` module imports `pydantic` or `yaml` except `contract/manifest.py`. |
| Only the dispatcher and skill tests import the SKILL.md parser | Only `dispatcher`, `skills.tests` and `contract.tests` import `contract.manifest` directly (`protected`). |

The contracts have no exceptions. The only `ignore_imports` entries are `contract.manifest -> pydantic` and `contract.manifest -> yaml` in the skills and contract contracts: the SKILL.md parser runs in the dispatcher only, so these are part of the rule, not imports to be removed. An ignored import also hides every chain through it, which is why the parser has its own `protected` contract: neither a skill nor another `contract` module reaches `pydantic` or `yaml` through it.

import-linter counts a lazy import inside a function as an import, so the chain `services.robot.backends.real -> services.robot.backends -> services.robot.backends.sim -> services.robot.backends.faults` (through `load()`, which imports both backends lazily) would break the two fault contracts above on any path that reaches the backend package; they therefore check direct imports only. The runtime proof that a real process never loads fault code is `test_real_loads_without_the_sdk_the_sim_or_fault_code` in `services/robot/tests/unit/test_backends.py`: in a fresh interpreter, `backends.load()` on the real backend and the robot service module load none of `unitree_sdk2py`, `services.robot.backends.sim`, `services.robot.backends.faults`, `services.backend.stub_state`, `contract.stub` and `contract.faults`. Skill faults (`skills/runtime/faults.py`) are internal to `skills` and need no contract.

Limits. A forbidden contract names the packages it bans, so the contracts do not show that the `contract` modules other than the parser are standard library only: a third package other than `pydantic` and `yaml` would pass. The `protected` detector contract and the dispatcher's stub-world contract check direct imports only: a chain from the dispatcher into `services.backend` through a robot module passes that contract and is bounded only by the robot modules the dispatcher contract forbids. import-linter records `from tests.helpers.fake_clock import Clock` as an import of that module, not of the `tests.helpers` package that Python also loads, which is why `tests/helpers/__init__.py` itself is in the helpers contract.

`test_fresh_interpreter_import_is_side_effect_free` in `tests/integration/test_skills.py` imports every module of `skills`, `services.robot`, `services.vision`, `services.backend` and `contract` (not their tests) except `contract.manifest` (which no skill process loads) in a fresh interpreter and checks that nothing is printed, no listed heavy module (`unitree_sdk2py`, `cv2`, `ultralytics`, `numpy`, `cyclonedds`) is loaded, and each skill's `POLICY` name matches its module. It names the client-surface modules, the `services.backend` modules, the dispatcher's stub control, the services, the backends, the detectors and the utilities, so a renamed module fails it.

### Coverage

```bash
uv run pytest --cov              # branch coverage of dispatcher, skills, contract, services and analysis, with missing lines
```

Coverage is opt-in (`pytest-cov`; settings in `[tool.coverage.*]` in `pyproject.toml`). It measures branches, and it also measures the skill, utility and CLI subprocesses the integration tests start (coverage's `[run] patch = ["subprocess", "_exit"]`; `_exit` is needed because skills end with `os._exit`). Processes killed with SIGKILL (timeouts, stops) record nothing. A covered run takes about 20 s instead of about 12 s.

The default run must pass on any machine after `uv sync` (core + dev dependencies only). CI (`.github/workflows/tests.yml`) runs `uv run lint-imports` and then the default run on every push to any branch, on Python 3.10, after `uv sync --locked`. Every test has a 30 s timeout (`pytest-timeout`). Parallel runs are opt-in (see design decisions below).

## Layout

| Folder | What |
|---|---|
| `dispatcher/tests/unit/` | config, process lock, registry, tool schema, plan validation (`dispatcher/planning/`: schema, validation, bounds, budget, verification), precheck, render + context, transport start-up (planner choice, initial posture), prompts (fixed-texts golden file), LLM client (mock HTTP), dispatcher loop, `format_outcome`, Telegram handlers, execution: response parsing (`test_parse_response.py`), stop path (`test_stop_path.py`), kill switch (`test_kill_switch.py`), `StepResult` serialisation (`test_step_result_dump.py`) |
| `dispatcher/tests/golden/` | expected catalog, context and fixed texts |
| `dispatcher/tests/helpers/` | dispatcher-only helpers (below) |
| `skills/tests/unit/` | shared skill helpers (policy, `parse_params`, state sampling), the mapping of the robot service's primitive replies to outcomes with a fake service session, `build_response` argument checks and message cutting, the catalog's `SKILL.md` bodies against the code constants |
| `services/robot/tests/unit/` | the robot service protocol and lease in process, its motion loop with a fake sport client (`services/robot/tests/fake_sport.py`), the `stop_move` utility, real-robot state mapping (`services.robot.backends.real`) and the simulated sport client (odometry, obstacle distances, robot faults) |
| `services/robot/tests/robot/` | reserved for opt-in robot tests; empty in v1 (the robot checks are manual) |
| `services/backend/tests/unit/` | the stub state file and the DDS init (`services.backend`) |
| `services/vision/tests/unit/` | the vision service protocol in process, the stub camera's projection and the box classification (`services/vision/boxes.py`) |
| `contract/tests/` | unit tests of the `contract` package only |
| `analysis/tests/` | unit tests of the run-log report (`analysis`) on log files written by the tests; imports only `analysis`, `dispatcher.runlog`, `dispatcher.config` and `contract` |
| `tests/integration/` | cross-service tests: executor, end-to-end with the real executor, CLI subprocesses (including "the CLI does not import `anthropic`"), live LLM, per-skill contract (one subprocess per skill), stub behaviour, one subprocess case per shared mechanism (invalid params, backend not configured, faults), utilities, the robot service as a subprocess, orphan watchdog, side-effect-free imports, and in process: skill policies against the registry (timeout covers the motion), `build_response` output against `SkillResponse`, walk and turn through the robot service's motion loop over the sim (`test_skills_on_sim.py`) |
| `tests/helpers/` | helpers shared by the root tests and the package suites; no project imports (below) |
| `tests/pytest_plugin.py` | the `--run-live`, `--run-robot` and `--update-golden` options, marker gating, the `update_golden` fixture and the network guard |
| `tests/test_network_guard.py` | the network guard refuses an outbound connection |
| `tests/test_report_skill_sets.py` | the report's default skill lists (`analysis.report`) against the registry's `moves` declaration; the `go2 report` CLI cases are in `dispatcher/tests/unit/test_report.py` |
| `.claude/hooks/tests/` | the Claude Code hooks, run as subprocesses ([hooks README](../../.claude/hooks/README.md)); in `testpaths`, so the default suite runs them |
| `.claude/scripts/tests/` | the Claude Code helper scripts ([scripts README](../../.claude/scripts/README.md)); in `testpaths`, so the default suite runs them |

A package folder holds unit tests of that package only, under the same import rules as the package ([import boundaries](#import-boundaries)): `skills/tests/` never imports `dispatcher` and reaches `robot` and `vision` only through their client surfaces, `services/robot/tests/` and `services/vision/tests/` import neither `skills`, `dispatcher` nor each other, `services/backend/tests/` imports neither service, `contract/tests/` imports only `contract`, while `dispatcher/tests/` import `skills` no more than the dispatcher does; every test folder may import `contract`. A test that needs more than its package allows (walk and turn on the robot service's motion loop), or that runs the services as real subprocesses, lives in the root `tests/integration/`; only the tests that start subprocesses carry the `integration` marker (see design decisions below).

## Markers and flags

| Marker | Meaning | Enabled by |
|---|---|---|
| `integration` | spawns real subprocesses with the stub backend | always runs |
| `live_llm` | calls the real Anthropic API | `--run-live` and `ANTHROPIC_API_KEY` |
| `robot` | needs the real Go2 | `--run-robot` |

Options (defined in the pytest plugin `tests/pytest_plugin.py`, loaded by `addopts = "-p tests.pytest_plugin"` in `pyproject.toml`, so they are available whichever folder is run):

- `--run-live`: run `live_llm` tests. They are skipped without it, and also skipped if `ANTHROPIC_API_KEY` is not set.
- `--run-robot`: run `robot` tests. There are none in v1; the robot checks are the supervised checklist in [robot.md](../../services/robot/docs/robot.md#supervised-robot-checklist).
- `--update-golden`: rewrite the golden files instead of comparing against them. Review the diff with `git diff dispatcher/tests/golden` before committing.

### Network guard

The plugin's autouse fixture `_block_network` makes the default suite offline by force: in every test, `socket.socket.connect`, `connect_ex` and `socket.getaddrinfo` raise `NetworkBlockedError` (naming the test and the opt-in flags) for any non-loopback IPv4/IPv6 address or non-local host name. Loopback addresses, `localhost` and Unix sockets (asyncio's self-pipe, `socketpair`) stay allowed. Tests marked `live_llm` or `robot` are exempt; they run only with `--run-live` or `--run-robot`, so the flag is what opts them out. The guard applies to the pytest process only: subprocesses started by integration tests are not patched (the stub backend needs no network). `tests/test_network_guard.py` checks that an outbound connection fails.

### Live LLM test

```bash
ANTHROPIC_API_KEY=sk-ant-... uv run pytest --run-live -s tests/integration/test_live_llm.py
```

It runs the task "turn left 90 degrees, then tell me if you see a chair" on the stub with `stub.objects = {chair = {x = 0.0, y = 2.0}}` (a chair 2 m to the robot's left, out of view until the turn). It passes if the task ends `DONE`, the message mentions the chair, and the run log has no `plan_invalid` record (which includes horizon rejections; [run-log.md](../../dispatcher/docs/run-log.md)). With `-s`, it prints the run log path.

It uses the default LLM settings ([llm.md](../../dispatcher/docs/llm.md#the-request)). **It has not been run live yet**; running it is the pending check of the request parameters ([roadmap.md](../../docs/roadmap.md#pending-human-work)).

## Helpers

Import them with absolute imports: `from tests.helpers import ...` for shared helpers (`tests/helpers/`) and `from dispatcher.tests.helpers import ...` for dispatcher-only ones (`dispatcher/tests/helpers/`); root tests may import both. `tests`, `dispatcher.tests`, `skills.tests`, `services.robot.tests`, `services.vision.tests`, `services.backend.tests`, `analysis.tests` and `contract.tests` are packages (each test folder has an `__init__.py`), and the editable install from `uv sync` puts the repo root on `sys.path`, so there is no `pythonpath` setting and no `sys.path` manipulation in the tests. `tests/helpers/` imports no project package, so every package suite except `contract/tests/` (which imports only `contract`) may use it.

Shared (`tests/helpers/`): `REPO_ROOT`, `TEST_TIME_SCALE`, and `fake_clock.Clock`, a monotonic clock that moves only when the test advances it, for the clock seams of `robot` and `skills` (`from tests.helpers.fake_clock import Clock`). Root integration tests only: `tests/integration/skill_process.py` (`stub_env`, `run_module`, `run_utility`, `single_response`; it imports `dispatcher.config` for the secret names it strips from the environment); `tests/integration/robot.py` (`robot.running(env)`, which starts the robot service for a `stub_env` env and yields a handle whose `arm()` returns an env with an armed lease); and `tests/integration/vision.py` (`vision.running(env)`, which starts `python -m services.vision.service` for a `stub_env` env, waits for its ready line and yields the env with `GO2_VISION_SOCKET` set, so `detect_object` can run as a subprocess without the dispatcher; `start` and `stop` are its parts). Dispatcher-only (`dispatcher/tests/helpers/`): everything else in the table.

| Helper | Purpose |
|---|---|
| `make_config(tmp_path, **sections)` | A `Config` with `log.dir` and `stub.state_file` under `tmp_path`, `stub.time_scale = 0.01`, and base dir = repo root (so `skills/<name>/SKILL.md` load). Overrides are section dicts, for example `make_config(tmp_path, loop={"max_failures": 1})`. |
| `ScriptedPlanner(items, surface, on_call=None)` | Implements `PlannerClient`; `surface` is the `PromptSurface` it reports (`make_runlog` and `app.build_dispatcher` build the run-log session from it). Items are returned in order: a `Plan` → its dump as raw tool input; a `dict` → raw tool input; the loop validates both; an exception instance → raised. Records every call's kwargs in `.calls`. `on_call(call_index)` runs before it returns (for example to set the stop event during a call). Raises `AssertionError` if called more often than scripted. Usage is `{"input_tokens": 100, "output_tokens": 20}`. |
| `make_runlog(cfg, planner, factory=RunLogFactory)` | Builds the `RunLogFactory` that `app.build_dispatcher` gives the dispatcher: log dir from `cfg`, a fresh session id, `SessionInfo` from `planner.surface`. Every `Dispatcher(...)` in a test passes it as `runlog=`. `factory` is a `RunLogFactory` subclass, for tests whose log fails, on `open` or on the `task_end` write. |
| `FakeClock(start=1000.0)` | `now()`, `advance(seconds)`. |
| `FakeExecutor(results, *, stop_move_ok=True, stop_move_posture=None, on_kill=None)` | Returns scripted `ExecResult`s. An item can also be an exception (raised) or a callable `f(call_kwargs) -> ExecResult` (to block, set the stop event, or advance the clock). Records `runs`, `kills`, `stop_moves`. `exec_result(...)` and `stop_move_result(...)` build results. |
| `fake_update`, `fake_context`, `replies` | Minimal Telegram `Update` / `Context` stand-ins with an `AsyncMock` `reply_text`. |
| `stub_env`, `run_module`, `run_utility`, `single_response` | Run a skill (`python -m skills.<name>`) or a utility (`python -m services.robot.<name>`) in a subprocess with a stub environment and parse its single response line. |
| `planner_factory.factory(surface)` | Builds a `ScriptedPlanner` from env `GO2_TEST_SCRIPT` (JSON list of raw tool inputs). Used by CLI subprocess tests through `GO2_TEST_PLANNER=planner_factory:factory` with `dispatcher/tests/helpers` on `PYTHONPATH` (a top-level import, so the subprocess does not load the whole helpers package). |
| `skill_modules/` | Small modules used as skill entrypoints in registry tests (`dispatcher.tests.helpers.skill_modules.<name>`) (the module-level entrypoints; `vision_pkg/` is a package entrypoint). The test skill sets with a `SKILL.md` per skill live in `tests/integration/alt_skills/{peek,spin}/`, and the executor integration test's `env_dump` (reports its environment keys) is `tests/integration/env_dump.py`, run as `tests.integration.env_dump`. |

The test planner hook also works by hand, for a stub run with no API key:

```bash
GO2_TEST_PLANNER=planner_factory:factory PYTHONPATH=dispatcher/tests/helpers \
GO2_TEST_SCRIPT='[{"status":"PLAN","steps":[{"skill":"turn","params":{"direction":"left"}}]},{"status":"DONE","steps":[],"message":"Turned."}]' \
uv run go2 run "turn left"
```

## Fault injection in tests

The stub faults ([service.md](../../services/robot/docs/service.md#fault-injection)) drive the executor and end-to-end tests through `stub.faults` (by dispatched step). For one process, a skill fault (`error`, `hang`, `crash`, `garbage`; [skills.md](../../skills/docs/skills.md#skill-faults)) goes in the `GO2_STUB_FAULT` env var, and a robot fault goes in the fault file before the `arm` (`contract.faults.write_fault`; in tests, `robot.running(...).arm(fault=...)` from `tests/integration/robot.py`).

## Golden files

`dispatcher/tests/golden/` holds the exact catalog (`catalog.txt`), the exact user messages for each context case (`context_*.txt`) and every fixed text in `prompts.py` rendered with example arguments (`fixed_texts.txt`). Any change to the wording in `prompts.py`, the renderer, or a `SKILL.md` changes them. Run `uv run pytest --update-golden`, review the diff, and remember that changing the prompt surface changes the registry hash and makes runs incomparable across the change ([skills.md](../../skills/docs/skills.md#catalog-and-registry-hash)). The fixed texts must stay identical across experimental conditions ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md)).

## Robot checks

There are no automated robot tests in v1. The real backend is checked by hand with the supervised checklist in [robot.md](../../services/robot/docs/robot.md#supervised-robot-checklist), where its results are also recorded.

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

- **SDK-calling code is tested in process with fakes.** The stub's `error` fault fails only the first SDK call, so mid-loop failures, a failing `StopMove` and an exception during `Move` in the robot service's motion loop (`services.robot.service.Motion`, driven with a fake sport client), the refused and failed primitive replies in `motion.move` and `motion.single_action` (via a fake `services.robot.client.session` and `backend.sleep`), and the failure branches of `stop_move.main()` (via a fake backend returned by `services.robot.backends.load` and `contract.runtime.emit`, which would otherwise exit the process) are reached only by monkeypatching. Adding faults to the stub for these paths was rejected: it would grow the stub for test-only behaviour and still cost one interpreter start per case.
- **Package folders hold unit tests of their own package; cross-service tests live in a root `tests/` package.** `skills/tests/` must not import `dispatcher`, and `services/robot/tests/` and `services/vision/tests/` must not import `skills`, so that the dependency direction of the code (the dispatcher imports skills, skills import the client surfaces of robot and vision, never the reverse) also holds for the tests. Integration tests drive both services (the executor starts skill processes, skill responses are parsed by the dispatcher's models), so they are cross-service by nature and all live in `tests/integration/`, together with the in-process tests that check skills against the dispatcher's registry or models. Rejected: keeping each test in the service whose code it mostly exercises, which made `skills/tests/` import `dispatcher`.
- **Pytest options come from a plugin module (`-p tests.pytest_plugin`), not a root `conftest.py`.** pytest registers an option only once, so the options cannot be defined in each suite's conftest, and a conftest is loaded only for paths below it, so a conftest in one suite is not loaded when another suite is run on its own. A root `conftest.py` worked but sat outside every test folder; the plugin keeps all test code under `tests/` and makes the options available for any subset (`uv run pytest skills/tests`). Helpers are imported with absolute package paths instead of a `pythonpath` entry, which avoids a bare top-level `helpers` module that could shadow other names.
- **The default suite blocks network access with an in-repo socket guard.** An autouse fixture in the plugin patches `socket.socket.connect`, `connect_ex` and `socket.getaddrinfo` for each test, so an accidental call to the Anthropic API, Telegram or the robot fails at once with a clear error instead of passing silently or hanging until the timeout. Exemption is by marker (`live_llm`, `robot`), which the flags already gate. Rejected: the `pytest-socket` dependency, which does the same with more options (host allow-lists, `--disable-socket`) the suite does not need; and blocking `socket.socket` creation outright, which would break asyncio and loopback use. The guard does not cover subprocesses or connectionless UDP `sendto`; both would be added only if a test is found reaching the network that way.
- **Coverage is opt-in, not in `addopts`.** Measuring subprocesses makes the run about 60 % slower, and the default run must stay fast.
- **`ruff format` is the formatter, with its defaults and line length 120** (not 88, which matches the existing code). One consistent style outweighs the hand-aligned trailing comments it removed. The formatter does not change string values, so the fixed texts in `dispatcher/prompts.py` and the registry hash are unaffected; that file stays exempt from `E501` because its fixed-text lines are long by design.
- **CI runs the import contracts and the default suite; lint and format stay out of CI.** The local Claude Code hook and the pre-commit check are the lint gate. The import contracts are also checked by a local hook ([hooks README](../../.claude/hooks/README.md)), but still run in CI because a broken boundary is a design error, not a style issue, and must not reach a branch through a commit made outside Claude Code. Live LLM tests would put the API key into CI secrets and cost money per push, and robot tests need a supervised session ([safety.md](../../docs/safety.md)); both stay opt-in and manual. The `robot` and `vision` extras are not installed in CI, which also checks that the default run needs none of them.
- **The package import boundaries are checked by `import-linter` contracts.** They follow indirect imports through every first-party module, whether or not a test executes it; third-party packages are leaf nodes, so a chain such as `anthropic -> pydantic` is not seen ([skills.md](../../skills/docs/skills.md#no-side-effects-on-import) owns the rule and this gap). Rejected: a ruff `TID251` banned-api rule, which sees only direct imports; a `sys.modules` check after importing every skill module, which covers only the executed import paths and cannot check for `pydantic` because the dispatcher and the parser tests load it; and Tach, which is unmaintained. The parser's `pydantic` and `yaml` imports are allowed with `ignore_imports`, which removes those two imports from the graph and with them every chain through `contract.manifest`, so the parser has its own `protected` contract. Rejected: listing every `contract` module except the parser as a source, which a new module would silently escape.
- **The contracts are package-level, without exceptions.** A source is a whole package (`skills`, `services`, `dispatcher`, `contract`, `tests.helpers`), so a new skill module is covered without editing `pyproject.toml`, and the package's tests are held to the package's rules. An import that crosses a boundary is removed, not ignored; the parser's `ignore_imports` entries are part of its rule ([import boundaries](#import-boundaries)). Rejected: listing the skill modules by name as sources, which a new skill silently escapes.
- **`tests/helpers/` imports no project package; process helpers that need the dispatcher live in `tests/integration/`.** The unit tests of `robot`, `skills` and `vision` import shared helpers, and Python loads `tests/helpers/__init__.py` with any of them, so a `dispatcher` import there makes every package suite depend on the dispatcher at run time while import-linter sees no such edge. `skill_process.py` needs `dispatcher.config.SECRET_ENV` and is used only by root integration tests, so it sits beside `robot.py` and `vision.py`. Rejected: a lazy import inside `stub_env`, which import-linter still records for `tests.helpers`; and an `ignore_imports` entry, which hides the chain instead of removing it.
- **This doc lives in `tests/docs/`.** The test-infrastructure owner (`tests-dev`) owns a doc tree in its own directory, like the `dispatcher` and `skills` packages. Rejected: keeping it in the shared `docs/`, which blurred its ownership.
