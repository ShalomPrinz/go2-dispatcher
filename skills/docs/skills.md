# Skills

The skill contract: how a skill is declared, invoked and answers, how the catalog the model sees is generated, the stub backend and fault injection, and how to add a skill. Robot-side behaviour (SDK calls, real backend, posture rule, settle waits) is in [robot.md](robot.md).

A skill has two parts:

1. `skills/catalog/<name>/SKILL.md`: YAML frontmatter that the registry reads, plus prose for humans. The prose is never sent to the LLM.
2. `skills/<name>.py`: a module with a `main()` that runs as a subprocess, and a module-level `POLICY` object that the dispatcher reads.

v1 skills: `walk`, `turn`, `sit`, `stretch`, `detect_object`. Two utilities, `stop_move` and `read_state`, run the same way but are not skills: they have no `SKILL.md` and the model never sees them. The registry loads every subfolder of `skills.dir` that has a `SKILL.md`, so a different skill set (for example another granularity tier) is a different folder ([running.md](../../docs/running.md)).

## SKILL.md frontmatter

The YAML block between the first two lines that are exactly `---` (the file must start with `---`), parsed with `yaml.safe_load`. The schema (`ParamSpec`, `SkillFrontmatter`) and the parser (`parse_skill_file`) live in `skills/frontmatter.py`; the dispatcher's registry calls them and adds the checks that need the folder or the module.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string matching `^[a-z][a-z0-9_]*$` | yes | Must equal the folder name and `POLICY.name`; this also makes names unique, so there is no separate duplicate check. |
| `entrypoint` | string | yes | Module run with `python -m`, for example `skills.walk`. |
| `description` | one-line string | yes | Shown in the catalog. |
| `params` | mapping name → ParamSpec | no | Parameters, in catalog order. Absent means no parameters. |
| `expect` | any | no | Reserved for v2 verification. Accepted and ignored. |

ParamSpec (parameter names match `^[a-z][a-z0-9_]*$`):

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `type` | `number` \| `string` \| `enum` | all | Required. |
| `description` | non-empty string | all | Required. Short. |
| `values` | non-empty list of unique lowercase strings | `enum` | Required for `enum`, not allowed otherwise. |
| `min`, `max` | finite number | `number` | Optional, inclusive, `min <= max`. |
| `default` | value of the param's type | all | Optional. If absent, the parameter is required. Must pass the param's own checks. |
| `unit` | non-empty string | `number` | Optional. Shown in the catalog. |

Any other key, a missing required key or an invalid value is a registry error, as are: an entrypoint that cannot be imported, a module without `POLICY`, a `POLICY` that is not a `SkillPolicy`, `POLICY.name` ≠ `name`, a missing skills folder, or zero skills. Transports print `Registry error: <message naming the file>` and exit 2. A frontmatter error reads `<path>: <dotted.loc>: <msg>` (e.g. `params.distance_m.min`); an extra key reads `unknown key`. The parser raises these as `SkillFileError`, and the registry re-raises them unchanged as registry errors.

`skills/catalog/walk/SKILL.md` is a full example. The dispatcher checks every planned step against these declarations before anything runs ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)).

## Catalog and registry hash

The catalog (`Registry.catalog_text()`) is generated from the frontmatter, skills sorted by name, one line per parameter, no trailing newline. It is sent as `system[1]` = `"## Skills\n" + catalog`. The current catalog is the golden file `dispatcher/tests/golden/catalog.txt`.

Type phrases: `one of a, b`; `text`; `number from X to Y`, `number, at least X`, `number, at most Y` or `number`, followed by the unit.

The **registry hash** is the first 16 hex characters of SHA-256 over `system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True)`. It identifies the whole prompt surface, so it changes with the skill set, any `SKILL.md` wording, the system text and the horizon (which appears in the system text and the tool schema). It is logged in `task_start` and `index.jsonl`; runs with different hashes are not directly comparable. `go2 catalog` prints the system text, catalog, tool schema and hash.

## Invocation

```
python -m skills.<name> '<params-json>'
```

- `argv[1]` is a JSON object with the **filled** params: checked, normalised and with defaults filled in by the dispatcher. Always present (`{}` for no params).
- Environment, set by the executor through `skills.env.child_env()` (secrets `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` removed by the executor first). `skills/env.py` defines every variable name below; skills and backends read them only through its constants:

| Variable | Meaning |
|---|---|
| `GO2_BACKEND` | `real` or `stub`. Missing or unknown → `backend_not_configured`. |
| `GO2_IFACE` | Network interface (real backend). |
| `GO2_YOLO_WEIGHTS` | Path to the YOLO weights. |
| `GO2_STUB_STATE_FILE` | Stub state file. |
| `GO2_STUB_TIME_SCALE` | Stub time scale. |
| `GO2_STUB_DETECTIONS` | JSON `{class: "position:closeness"}`. |
| `GO2_STUB_FAULT` | Fault kind; set only for the faulted step, never inherited from the dispatcher's environment. |
| `GO2_PARENT_PID` | Dispatcher PID, for the orphan watchdog. |
| `PYTHONUNBUFFERED` | `1`. |

- The process runs with the base dir as working directory, in its own session (`start_new_session=True`), so a kill reaches the whole process group ([safety.md](../../docs/safety.md#the-stop-path)).
- **Output:** exactly one JSON line on the real stdout, written by `runner.emit()` through the stdout fd that `skills/process.py` saves. Everything else goes to stderr: `process.capture_stdout()` points fd 1 at stderr first, so output from the SDK, CycloneDDS, ultralytics or C code cannot corrupt the response line. The executor reads the last non-empty stdout line and requires its `skill` to match.
- **Exit code:** 0 for `status = "ok"`, 1 for `status = "error"` (`emit` uses `os._exit`, because DDS threads can hang normal interpreter shutdown). No valid response line means step outcome `malformed`.
- Skills do not validate params: they read them directly (`params["direction"]`) and apply no defaults. The dispatcher validates every plan against the `SKILL.md` types, enums and ranges before it runs a skill ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)). A skill run by hand is therefore not range-limited and needs all params; a missing key or wrong type ends as `exception`. Only `detect_object.target` is trimmed and lower-cased by the skill.

## Response schema

Every skill and utility prints one `SkillResponse` (`schema_version` 1). `SkillResponse`, `SkillError` and `RobotState` are defined once, as frozen standard-library dataclasses in `skills/schema.py`; the dispatcher re-exports them from `dispatcher/models.py`. The response is built by `runner.build_response(skill, outcome, states, total_ms)` (`skills/runner.py`) from its three sources and printed with `runner.to_json` (`json.dumps(dataclasses.asdict(...), allow_nan=False)`):

- `outcome`, the frozen `SkillOutcome` the body returns: `status`, `observations`, `error`, and the body's own timing keys. An exception that escapes the body becomes `SkillOutcome.from_exception(e)`: `InvalidParams` maps to `invalid_params`, `BackendNotConfigured` to `backend_not_configured`, anything else to `exception` with message `<ExceptionType>: <first line>` (`runner.describe_exception`) and its traceback on stderr.
- `states`, the `runner.StateSampler` that `run_main` creates before the body runs: `state_before`, `state_after`, `state_error` (the failed samples' `<label>: <ExceptionType>: <first line>`, joined with `; `) and the time spent sampling. `states.take("before" | "after")` samples into that field; `strict=True` re-raises a failed sample instead of recording it. `BackendNotConfigured` always propagates. Because `run_main` owns the sampler, a sample taken before an exception is still reported.
- `total_ms`, measured by `run_main` from its start to `emit`.

`timing` is the outcome's keys, then `state_ms` if a sample was attempted, then `total_ms`. `run_main(skill, body, *, watchdog=True)` runs `body(states)` and emits; `run_skill(policy, body, *, sample_state=True)` wraps it for skills (parse params, sample before, `body(params)`, sample after). The utilities pass their own body to `run_main`: `stop_move` returns its `invalid_params` error after `StopMove()` so `sdk_ret` and `stop_call_ms` are kept, and `read_state` samples strictly and maps a failure to `state_unavailable`.

Constructing a `SkillResponse` checks only that `error` is present if and only if `status = "error"`. `build_response` also raises `ValueError` for `status = "error"` without `error_code` or `error_message`; `emit` turns any exception while building or serialising the line into an `exception` error line without the body's observations and states. The dispatcher validates the whole line against the same classes with a pydantic `TypeAdapter`: types, enums, and no unknown keys at any level, including inside `error` and the state objects. A line that fails it is step outcome `malformed`.

| Field | Meaning |
|---|---|
| `schema_version` | Always `1`. |
| `skill` | Skill name; must match the skill that was run. |
| `status` | `ok` or `error`. |
| `observations` | Skill-specific results. |
| `error` | `{code, message}` if and only if `status = "error"`, else `null`. `message` is collapsed to one line (whitespace runs become one space) and not length-limited; the dispatcher cuts it where it shows it ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message)). |
| `state_before`, `state_after` | Robot state sampled at start and end ([robot.md](robot.md#state-sampling)); utilities have `state_after` only. |
| `state_error` | Why a state sample failed (`before: ...` / `after: ...`, joined with `; `), else `null`. |
| `timing` | `init_ms`, `exec_ms`, `state_ms`, `total_ms`; `stop_move` adds `stop_call_ms`, from utility start to the return of `StopMove()`. `state_ms` is present only if a state sample was attempted (absent, for example, when params are invalid). |

Robot state fields, their sources and the posture rule are in [robot.md](robot.md#state-sampling). State is **logged only**; the model sees only the derived posture ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message)).

Run a skill by hand ([below](#running-a-skill-by-hand)) to see a full response.

## The skills

| Skill | Action | Observations | Shown to the model on `ok` |
|---|---|---|---|
| `walk` | `Move` for `distance_m / VELOCITY_MPS` s, then `StopMove` | `direction`, `distance_m`, `duration_s` (commanded), `sdk_ret`, `orphaned` (only if set) | nothing |
| `turn` | `Move(0, 0, ±YAW_RATE_RPS)` for `radians(angle_deg) / YAW_RATE_RPS` s, then `StopMove` | `direction`, `angle_deg`, `duration_s`, `sdk_ret`, `orphaned` | nothing |
| `sit` | `StandDown()`, then a settle wait | `sdk_ret` | nothing |
| `stretch` | `Stretch()`, then a settle wait | `sdk_ret` | nothing |
| `detect_object` | one camera frame + YOLO; never moves | `target`, `object_found`, `position` (left/center/right), `closeness` (near/medium/far), `confidence` | `object_found`, `position`, `closeness`, `confidence` |

Params and ranges are in each `SKILL.md`. Walk and turn are open-loop ([robot.md](robot.md#motion)). Not finding an object is `status = ok` with `object_found: false`, not an error.

### Error codes

The codes are the members of `ErrorCode` in `skills/schema.py`, a `str` enum: skills pass members, the response line carries the plain string, and `SkillError.code` stays a `str`.

| Code | Skills | Meaning |
|---|---|---|
| `invalid_params` | all | The params argument is missing, not valid JSON, or not a JSON object. Keys and values are not checked by the skill (the dispatcher validates them). |
| `sdk_error` | walk, turn, sit, stretch, stop_move | An SDK call returned non-zero. Message `<Call> returned <code>` (walk/turn add `; StopMove returned <code>` if the cleanup stop also failed). Motion skills send `StopMove` first. |
| `backend_not_configured` | all | `GO2_BACKEND` missing or unknown, or `GO2_IFACE` empty on the real backend. |
| `exception` | all | Unexpected exception: `<Type>: <first line>`. The traceback goes to stderr. |
| `unsupported_object` | detect_object | Target is not one of the 80 COCO classes, for example `'phone' is not a detectable object. Closest supported: cell phone, spoon, horse.` (up to 3 close matches, if any). |
| `detector_error` | detect_object | Any other detector failure (`backend.DetectorError`; the specific cases below are its subclasses). |
| `camera_unavailable` | detect_object | The camera returned an error or no data (also the stub's `error` fault). |
| `bad_frame` | detect_object | The image could not be decoded. |
| `weights_missing` | detect_object | The YOLO weights file does not exist. |
| `state_unavailable` | read_state | No state could be sampled. |

Detector errors have the message `<ExceptionType>: <message>`. Outcomes the dispatcher adds on its own (`timeout`, `malformed`, `rejected`, `motion_budget_exceeded`, `interrupted`) are listed in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-outcomes).

## Policies

Each skill module defines one module-level `POLICY = policy.SkillPolicy(name, timeout, cost, context_observations)` (`skills/policy.py`). `timeout` and `cost` (a `MotionCost(distance_m, rotation_deg)`, default zero) are each a constant or a function of params; `context_observations` lists the observation keys shown to the model on `ok`. The dispatcher imports the module only to read `POLICY`.

The dispatcher reads only `name`, `context_observations`, `timeout_s(params)` and `motion_cost(params)`, with the filled, checked params. Every number is a module-level constant in `skills/<skill>.py`:

| Skill | Timeout | Motion cost |
|---|---|---|
| `walk` | `BASE_S + FACTOR × distance_m / VELOCITY_MPS` | `distance_m` |
| `turn` | `BASE_S + FACTOR × radians(angle_deg) / YAW_RATE_RPS` | `rotation_deg = angle_deg` |
| `sit`, `stretch`, `detect_object` | `TIMEOUT_S` | zero |

`BASE_S` covers process start, SDK init and the two state samples; `FACTOR` is a margin on the commanded motion time; `detect_object`'s `TIMEOUT_S` covers loading YOLO on CPU. The timeout covers the whole process from start to exit, including any [settle wait](robot.md#settle-waits). The motion cost feeds the motion budget ([safety.md](../../docs/safety.md#motion-budget)).

The utilities `stop_move` and `read_state` export a `POLICY` too, so every module the dispatcher runs carries its name and timeout the same way: `SkillPolicy(name, timeout=TIMEOUT_S)` with `TIMEOUT_S = 10.0` in each, zero motion cost and no `context_observations` (the defaults). They have no `SKILL.md`, so the registry never loads them; their `POLICY` is never in the catalog or the registry hash.

## No side effects on import

Importing a skill module must do nothing: no SDK import, no DDS init, no argument parsing, no output. All work happens in `main()`, under `if __name__ == "__main__": main()`. At top level a skill imports only the standard library and `skills`. Third-party imports (`unitree_sdk2py`, `cv2`, `ultralytics`, `numpy`) happen inside functions in `skills/real.py`. A test imports every `skills` module in a fresh interpreter and checks that none of these were loaded. This is what lets the dispatcher read policies without touching the SDK. `skills` never imports `dispatcher`, pydantic or PyYAML, directly or indirectly; the one exception is `skills/frontmatter.py`, which imports pydantic and PyYAML at top level and is imported only by the dispatcher and the skill tests, never by a skill.

The import-linter contracts enforce these rules before every commit and in CI for every import chain through the repo's own modules, including imports inside functions ([testing.md](../../tests/docs/testing.md#import-boundaries)). They treat third-party packages as leaves, so a third-party import that pulls in pydantic or PyYAML (`import anthropic`, at top level or inside a function) passes them, and the fresh-interpreter test checks only the modules it lists; code review is what enforces the import rule above for such imports. That gap is accepted because the rule exists to keep skill spawn time low and already forbids the import.

## Stub backend

The stub (`skills/stub.py`) replaces only the SDK layer inside the skill process; processes, timeouts, kills and `StopMove` run for real. Constants are in `skills/stub.py`. It keeps posture in a JSON state file (`{"posture": "standing" | "sitting"}`; a missing file means standing), written atomically.

| Call | While standing | While sitting |
|---|---|---|
| `Move` | 0 | 1 (not standing) |
| `StopMove` | 0 | 0 |
| `StandDown` | 0; posture → sitting; waits `STAND_DOWN_S` | 0; no change |
| `Stretch` | 0; waits `STRETCH_S` | 1 |

- `detect(target)` waits `DETECT_S` and returns found (`DETECT_CONFIDENCE`, position and closeness from the entry) if the target is listed in `stub.detections`, else not found.
- `sample_state()` returns a `body_height` per posture (`BODY_HEIGHT_*_M`); other fields are `null`.
- All stub waits go through `backend.sleep()` and are multiplied by `stub.time_scale`. Skills never call `time.sleep` themselves.
- The stub is reset to `stub.initial_posture` at startup and by `go2 --reset-stub`, not between tasks ([running.md](../../docs/running.md)). The dispatcher does this with `stub.reset(posture, path)`, its only access to stub state; start-up posture on either backend comes from the `read_state` utility.

### Fault injection

Faults exercise every failure path with real processes. They apply to the stub backend only (`stub.faults` must be empty with `robot.backend = "real"`; that is a config error).

Configure them in config:

```toml
[stub]
faults = [ { step = 2, kind = "hang" } ]
```

or on the command line with `--fault STEP:KIND` (repeatable), which replaces the whole list ([running.md](../../docs/running.md)).

- `step` counts **dispatched** steps in the task, 1-based, across plans. Rejected steps are not counted. Steps must be unique.
- The dispatcher sets `GO2_STUB_FAULT` for that step only. Inside the process, the fault hits the first action call the skill makes (`Move`, `StopMove`, `StandDown`, `Stretch` or `detect`); later calls behave normally.
- Utilities (`stop_move`, `read_state`) ignore faults (they call `stub.disable_faults()` first), so the stop path always works.
- The fault kinds are `stub.FaultKind` (`FAULT_KINDS`); the dispatcher's `stub.faults[].kind` config type is that same `Literal`, as `stub.initial_posture` is `stub.StubPosture`. The `stub.time_scale`, `stub.initial_posture` and `stub.state_file` config defaults are `stub.DEFAULT_TIME_SCALE`, `DEFAULT_POSTURE` and `DEFAULT_STATE_FILE`, the same values the stub uses when its env is unset.

| Kind | What the skill process does | Step outcome |
|---|---|---|
| `error` | the call returns 99 (`sdk_error`, e.g. `Move returned 99`); the detector raises a camera error (`camera_unavailable`) | `error` |
| `hang` | sleeps forever (not scaled) | `timeout` (killed, then `StopMove`) |
| `crash` | exits with code 139, no output | `malformed` |
| `garbage` | prints `not json` and exits 0 | `malformed` |

For one process run by hand, set `GO2_STUB_FAULT=<kind>` directly.

## Adding a new skill

Example: a `stand` skill (recommended before experiments, see [roadmap.md](../../docs/roadmap.md#open-questions)).

1. Create `skills/stand.py` by copying `skills/sit.py` (a single SDK action with a settle wait via `motion.single_action`). A real `stand` would call `StandUp()` then `BalanceStand()`. Each SDK method must exist on the stub (`StubSportClient`) and work through `real.get_sport_client()`.
2. Create `skills/catalog/stand/SKILL.md` with frontmatter (`name: stand`, `entrypoint: skills.stand`, a one-line `description`, `params` if any) and a short prose section.
3. If the skill moves the robot, pass `cost=` (a `MotionCost`, or a function of params returning one); a timeout that depends on params is a function too (see `skills/walk.py`). If some observations should reach the model, list them in `context_observations`.
4. Write `body(params)` and end the module with `runner.run_skill(POLICY, body)`, which parses params, samples state before and after, and emits the response. The body returns a frozen `runner.SkillOutcome`, built only with `SkillOutcome.ok(observations=..., timing=...)` or `SkillOutcome.error(code, message, observations=..., timing=...)` so a status without its matching error fields cannot be built; `timing` holds `init_ms` and `exec_ms`. Read params directly (`params["key"]`); do not re-check types, enums or ranges, which the dispatcher validates against the `SKILL.md`. Use `backend.sleep()`, never `time.sleep`. In motion loops, check `process.orphaned()` and always end with `StopMove()`.
5. Run `uv run go2 catalog`, run the skill by hand (below), and add tests (contract test in `tests/integration/test_skills.py`, policy test in `tests/integration/test_skill_policies.py`). The catalog golden file and the registry hash change: rewrite the golden file with `uv run pytest --update-golden` and review the diff ([testing.md](../../tests/docs/testing.md)).
6. Update this page (and [robot.md](robot.md) if the skill adds robot-side facts).

## Running a skill by hand

On the stub:

```bash
export GO2_BACKEND=stub GO2_STUB_STATE_FILE=runs/.stub_state.json GO2_STUB_TIME_SCALE=0.1
uv run python -m skills.walk '{"direction":"forward","distance_m":1}'
uv run python -m skills.detect_object '{"target":"chair"}'
GO2_STUB_DETECTIONS='{"chair":"center:near"}' uv run python -m skills.detect_object '{"target":"chair"}'
GO2_STUB_FAULT=crash uv run python -m skills.sit '{}'; echo "exit=$?"
uv run python -m skills.read_state '{}'
uv run python -m skills.stop_move '{}'
```

Without `GO2_STUB_STATE_FILE` and `GO2_STUB_TIME_SCALE` the stub uses `runs/.stub_state.json` (relative to the working directory) and 0.1. On the real robot set `GO2_BACKEND=real GO2_IFACE=<nic>` (and `GO2_YOLO_WEIGHTS` for detection), with the robot supervised ([safety.md](../../docs/safety.md#supervised-operation-rules)). Pass all params: skills do not fill defaults.

## Design decisions

- **One subprocess per skill call, never reused.** The Unitree SDK initialises DDS through a process-wide singleton, so a fresh process guarantees a clean channel. It also lets a hung call be killed and isolates crashes in the native bindings.
- **`walk` was split into `walk` and `turn`.** The predecessor's single range mixed metres and degrees. Separate skills give the model clearer parameters and allow separate travel and rotation budgets. `turn` uses **degrees** at the interface because that is what operators say; the skill converts to radians.
- **One common JSON response schema for all skills**, replacing the predecessor's three incompatible output formats, so the executor, the log and the context renderer handle every skill the same way.
- **Each skill owns its policy** (timeout formula, motion cost, observations shown to the model) as module constants next to the code that shares them, so a number like walking speed is defined once and tuned in one place.
- **A policy is one frozen `SkillPolicy` dataclass instance per skill, not a subclass per skill.** The skills differ only in data (a name, a constant or a small formula for timeout and cost, an observation list), so a value with optional callables says it in a few lines and the constants sit at module level where `run_skill(POLICY, body)` and the body use them without a class prefix. The dataclass lives in `skills/policy.py`, which imports only the standard library, so the dispatcher's own modules import the type without the runner. Rejected: a `SkillPolicy` base class with one `<Name>Policy` subclass per skill overriding `timeout_s()`/`motion_cost()`; it repeated boilerplate in every module and spread the constants across class attributes, without adding behaviour. The dispatcher still calls `timeout_s(params)` and `motion_cost(params)`, so it does not need to know whether a value is constant.
- **Utilities carry a `POLICY` like skills, as a plain `SkillPolicy` with the default cost and observations.** One mechanism for every module the dispatcher spawns, so the dispatcher reads a utility's name and timeout from its module instead of hard-coding them. The defaults fit both utilities (zero cost: neither commands motion), so a smaller utility-only type would add a second type without a field to drop that matters. Rejected: a `UTILITIES` map in `skills` (a second registry of names and timeouts beside the modules that own them); keeping utility timeouts as dispatcher config keys (`robot.*_timeout_s`), which gave timeouts two sources of truth, config for utilities and `POLICY` for skills.
- **Param and response validation happen on the dispatcher side; the response schema is defined once in `skills`.** Plan params are validated only by the dispatcher's plan and bounds validation against `SKILL.md`; skills read params directly. Skill responses are fully validated only by the dispatcher, but against the dataclasses in `skills/schema.py`, so the skill side constructs the very classes the dispatcher checks and there is no second definition to drift. The one rule that runs in both processes is the error-iff-status check in `SkillResponse.__post_init__`: it is part of the class, costs nothing, and pydantic reports its `ValueError` as a validation error. Rejected: separate skill-side validation of params or response types; the skill-side param checks only mattered for hand-run skills, where a missing key now reports `exception` instead of `invalid_params`.
- **The response schema is standard-library frozen dataclasses (`skills/schema.py`), validated by pydantic only in the dispatcher.** Skill processes must start fast and never import pydantic ([above](#no-side-effects-on-import)), yet the shape must be defined once. pydantic validates dataclasses directly; `__pydantic_config__ = {"extra": "forbid"}` is a plain dict, so `schema.py` needs no pydantic import. `RobotState` forbids extra keys like the other two: `asdict` and pydantic's dump would silently drop them, so a new robot field must be added to the class. A direct constructor call does no type checks, only `__post_init__`; type checks happen when the dispatcher parses the line. Rejected: pydantic models in `skills` (about 80 ms added to every spawn, `stop_move` included); dispatcher-only models plus a test that keeps the skill-side dicts in step (still two definitions); a JSON Schema file (needs code generation or a new dependency); `TypedDict` (pydantic needs `typing_extensions.TypedDict` on Python < 3.12); msgspec (a C dependency in the skill processes).
- **The `SKILL.md` schema and parser belong to `skills` (`skills/frontmatter.py`)**, so the package that defines the skills owns its catalog contract, and its rules are documented and tested here. pydantic is acceptable there because only the dispatcher process imports the parser; skill subprocesses never do, which an import-linter contract enforces for imports through the repo's own modules ([testing.md](../../tests/docs/testing.md#import-boundaries)). The registry hash stays in the dispatcher, because it also covers the system text and tool schema. Rejected: keeping the schema in the dispatcher's registry, which put the `SKILL.md` contract in the package that only consumes it.
- **The catalog is generated from the registry, never hand-written**, so skill sets of different granularity are presented to the model in the same form and stay comparable. The `SKILL.md` prose never reaches the model.
- **An unsupported `detect_object` target is a skill error with suggestions; the 80-class COCO list stays out of the catalog.** Listing every class would add tokens to every call; the skill validates the target and suggests close matches, so the model can correct itself on the next call.
- **State is sampled at the start and end of every step, for logging only.** v1 gives no verdicts. The samples provide data to set v2 verification thresholds before seeing any verification results, and supply the posture shown to the model ([roadmap.md](../../docs/roadmap.md#v2-plan)).
- **The stub replaces only the SDK layer inside the subprocess**, so process start, timeouts and kills are exercised for real offline. It remembers sitting or standing only: enough to run every failure path. Simulating motion is a v2 prerequisite ([roadmap.md](../../docs/roadmap.md#stub-upgrade-prerequisite)).
- **`skills` owns the process environment contract, the posture type, the error codes, the fault kinds and the stub defaults; the dispatcher imports them.** These are contracts between the executor and the skill processes, and the skill side is where they are read. Defining them once (`skills/env.py`, `schema.Posture`, `schema.ErrorCode`, `stub.FaultKind`, the `stub.DEFAULT_*` values) removes the copies in the executor and config that could drift silently: a renamed variable, a new fault kind or a changed stub default used to need matching edits in both packages. `ErrorCode` is `class ErrorCode(str, Enum)` with `__str__` returning the value, because Python 3.10 has no `StrEnum`; the members serialise and compare as their plain strings, so the response line and the dispatcher's validation are unchanged. `SkillError.code` stays `str`, so pydantic does not reject a code the dispatcher does not know. `Posture` lives in `schema.py` next to `RobotState`, which uses it, rather than in `backend.py`. Rejected: a dispatcher-owned env builder (it would keep the variable names in the package that only sets them); typing `SkillError.code` as `ErrorCode` (an unknown code would turn a skill error into `malformed`).
- **The response is built from three sources, `SkillOutcome` + `StateSampler` + `total_ms`, each written by the code that owns it.** The body owns the outcome, the sampler owns the state fields and their timing, and `run_main` owns the total time and the exception mapping (`SkillOutcome.from_exception`). `run_main` creates the sampler before calling the body, so samples taken before an exception survive without the body writing into a shared result. Accepted edge case: any exception in `stop_move` after `StopMove()` returns (the settle `backend.sleep` on a malformed `GO2_STUB_TIME_SCALE`, or the after-sample raising `BackendNotConfigured`) drops `sdk_ret` and `stop_call_ms` from the response; only misconfiguration reaches it. Rejected: a mutable `ResponseDraft` (a typed version of the old `out` dict: three writers still mutate one object, and it duplicates `SkillResponse`); plain locals in `run_main` with `try/finally` (it either duplicates the exception mapping in each utility or keeps utilities mutating a result argument).
- **The dispatcher touches stub state only through `stub.reset(posture, path)`, never the state file or `write_posture`.** The state file layout stays a stub internal, so a richer stub state (the v2 stub upgrade) changes `reset` alone, and posture is read the same way on both backends, through `read_state`. Rejected: a stub `read_posture` call in the dispatcher, which needs a backend branch and a second posture path.
- **The shared skill code is three modules with one reason to change each: `policy.py` (`SkillPolicy`, `MotionCost`, read by the dispatcher), `process.py` (the saved stdout fd and the orphan watchdog) and `runner.py` (`SkillOutcome`, `StateSampler`, response building, `emit`, `run_main`, `run_skill`).** The imports run one way, `runner` → `backend` → `stub` → `process`, so no module imports another inside a function to dodge a cycle; `backend` still imports `real` and `stub` lazily, to select the backend and keep third-party imports out of import time. `process` is not named `io`, so it never shadows the standard library module. Rejected: one `result.py` holding all of it, which mixed the dispatcher's policy reads with process plumbing and needed a lazy import cycle with `backend` and `stub`.
- **Skills collapse an error message to one line but do not limit its length; the dispatcher owns the limit.** One limit in one place, so a message is never cut twice to two different lengths, and the run log keeps the skill's full message in `response`. Rejected: a skill-side cap as well (it was 300 characters against the dispatcher's 200, so the skill cut was dead for every view except the logged response).
- **Fault injection by dispatched step number** (error, hang, crash, garbage) covers each step outcome the executor can produce, with real processes.
- **No `integer` param type.** No skill used it; a whole-number parameter is declared `number`. Full decision in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md).
