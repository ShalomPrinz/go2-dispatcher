# Contract

What the dispatcher, the skills and the services agree on, and the `contract` package that holds it: how a skill is declared (`SKILL.md`), invoked and answers, the error codes, the per-skill policy types, and the constants and wire framing that more than one package uses. The skills themselves, the catalog and registry hash, the stub and the services are in [skills.md](../../skills/docs/skills.md); robot-side behaviour is in [robot.md](../../skills/docs/robot.md).

## The package

`contract` imports no other project package (`skills`, `dispatcher`), and every module except `manifest` imports only the standard library.

| Module | Holds |
|---|---|
| `contract/manifest.py` | The `SKILL.md` schema and parser ([frontmatter](#skillmd-frontmatter), [body](#skillmd-body)). Imports pydantic and PyYAML; dispatcher process and tests only. |
| `contract/env.py` | The `GO2_*` environment variable names, `child_env()`, and the socket and lock paths ([Invocation](#invocation)). |
| `contract/schema.py` | `SkillResponse`, `SkillError`, `RobotState`, `ObstacleDistances`, `Posture`, `ErrorCode`, `SCHEMA_VERSION` ([Response schema](#response-schema), [Error codes](#error-codes)). |
| `contract/policy.py` | `SkillPolicy`, `MotionCost` ([Policies](#policies)). |
| `contract/wire.py` | The message framing the robot and vision service protocols share ([Wire framing](#wire-framing)). |
| `contract/constants.py` | `YAW_SIGN`, `Link` ([Shared constants](#shared-constants)). |

## SKILL.md frontmatter

The YAML block between the first two lines that are exactly `---` (the file must start with `---`), parsed with `yaml.safe_load`. The schema (`ParamSpec`, `SkillFrontmatter`, `SkillSection`, `SkillFile`) and the parser (`parse_skill_file`) live in `contract/manifest.py`; the dispatcher's registry calls them and adds the checks that need the folder or the module. `parse_skill_file` validates the frontmatter, then the [body](#skillmd-body), and returns a `SkillFile`: the `SkillFrontmatter` keys plus `sections`.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string matching `^[a-z][a-z0-9_]*$` | yes | Must equal the folder name and `POLICY.name`; this also makes names unique, so there is no separate duplicate check. |
| `entrypoint` | string | yes | Module run with `python -m`, for example `skills.walk`. |
| `description` | one-line string | yes | Shown in the catalog. |
| `params` | mapping name → ParamSpec | no | Parameters, in catalog order. Absent means no parameters. |
| `expect` | Expectation | no | Goal expectation the dispatcher checks a finished step against ([below](#expectations-and-preconditions)). Absent means the step is unverified. Not in the catalog. |
| `requires` | Precondition | no | Precondition the dispatcher checks before it dispatches a step ([below](#expectations-and-preconditions)). Absent means none. Not in the catalog. |

ParamSpec (parameter names match `^[a-z][a-z0-9_]*$`):

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `type` | `number` \| `string` \| `enum` | all | Required. |
| `description` | non-empty string | all | Required. Short. |
| `values` | non-empty list of unique lowercase strings | `enum` | Required for `enum`, not allowed otherwise. |
| `min`, `max` | finite number | `number` | Optional, inclusive, `min <= max`. |
| `default` | value of the param's type | all | Optional. If absent, the parameter is required. Must pass the param's own checks. |
| `unit` | non-empty string | `number` | Optional. Shown in the catalog. |

Any other key (`sections` included), a missing required key or an invalid value is a registry error, as is a body that breaks the [template](#skillmd-body), and so are: an entrypoint that cannot be imported, a module without `POLICY`, a `POLICY` that is not a `SkillPolicy`, `POLICY.name` ≠ `name`, a missing skills folder, or zero skills. Transports print `Registry error: <message naming the file>` and exit 2. A frontmatter error reads `<path>: <dotted.loc>: <msg>` (e.g. `params.distance_m.min`); an extra key reads `unknown key`; a body error reads `<path>: body: <msg>`. The parser raises these as `SkillFileError`, and the registry re-raises them unchanged as registry errors.

`skills/catalog/walk/SKILL.md` is a full example. The dispatcher checks every planned step against these declarations before anything runs ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)).

## Expectations and preconditions

`expect` and `requires` are frontmatter for the dispatcher's verification, which evaluates them against the step's state samples ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#verification)). They are typed (`Expectation`, `YawDelta`, `Precondition` in `contract/manifest.py`), strict like the rest of the frontmatter (unknown keys, nulls and wrong types are registry errors), and exposed on `SkillFile.expect` and `SkillFile.requires`. `catalog_text()` renders neither, so they change neither the catalog nor the registry hash; the planner reads preconditions from the body's `Requires` section.

**Expectation** (`expect`): a mapping with at least one key; every key given must hold. Measured on the step's own samples: `state_after`, and `state_before` for a delta.

| Key | Type | Holds when |
|---|---|---|
| `posture` | `standing` \| `sitting` | `state_after.posture` equals it. Posture is the body-height bound of the [posture rule](../../skills/docs/robot.md#posture-rule) (`POSTURE_SITTING_MAX_M`, `POSTURE_STANDING_MIN_M`, *tunable*), so the thresholds live in one place and an expectation follows the rule when it is retuned. |
| `mode` | integer, at least 0 | `state_after.mode` equals it. |
| `yaw_delta` | mapping: `param` (a declared `number` param with `unit: degrees`), `direction_param` (a declared `enum` param whose values are all keys of `YAW_SIGN`), `tolerance_deg` (number > 0) | The signed yaw change Δ = `state_after.imu_rpy[2]` − `state_before.imu_rpy[2]` (counter-clockwise +, the IMU and `vyaw` convention of the [`move` target](../../skills/docs/skills.md#robot-service)) is within `tolerance_deg` of the expected change E = `YAW_SIGN[direction] × param` in degrees, with the step's values after defaults: \|wrap(Δ − E)\| ≤ `tolerance_deg`, wrapped to (−180°, 180°]. `YAW_SIGN` (`contract/constants.py`, `left` +1, `right` −1) is the map `turn` commands with, so a turn the wrong way fails. |
| `self_reported` | `true` | Only key allowed when given. The skill's own response is the outcome (`status`); no state is checked. |

**Precondition** (`requires`): one key, `posture`, a non-empty list of unique postures (`standing`, `sitting`, `unknown`). It holds when the current posture is in the list; `unknown` passes only if listed.

Declarations (v1 skills):

| Skill | `expect` | `requires` | Why |
|---|---|---|---|
| `sit` | `posture: sitting` | none | Already sitting is not an error. The sitting `mode` is *unverified* (the stub's 5 is assumed), so it is not expected. |
| `stretch` | `posture: standing`, `mode: 0` | `posture: [standing]` | `SETTLE_S` makes `state_after` a sample at rest; `mode` 0 while standing at rest is seen on the robot ([robot.md](../../skills/docs/robot.md#state-sampling)). |
| `turn` | `yaw_delta: {param: angle_deg, direction_param: direction, tolerance_deg: 10}` | `posture: [standing]` | `tolerance_deg` 10 (*tunable*): the closed-loop turn ends within the 3° `YAW_STOP_MARGIN_RAD` plus 1–3° of coast, and the open-loop fallback spread 72.6–81.0° over six 90° turns ([robot.md](../../skills/docs/robot.md#design-decisions)), so 10° covers both. |
| `walk` | none (unverified) | `posture: [standing]` | No odometry source is decided; dead reckoning from the commanded velocity is never an expectation. |
| `detect_object` | `self_reported: true` | none | The detection is the measurement. |

## SKILL.md body

Everything after the closing `---` is the body: text for the model, written for the planner. It holds only what the planner needs to choose calls and params: preconditions (such as failing while sitting), effects and limits (such as speed or the COCO-class restriction). No SDK calls, rates, environment variables, settle constants or run commands; those are here and in [robot.md](../../skills/docs/robot.md). Params, ranges and the one-line `description` are already in the frontmatter and are not repeated.

The body is exactly these sections, each a line `## <heading>` followed by its text, in this order (`SECTION_HEADINGS`):

| Heading | Says |
|---|---|
| `Use when` | When the planner should pick this skill. |
| `Requires` | Preconditions; a call that breaks one fails. |
| `Effect` | What changes on success, including speeds. |
| `Limits` | What the skill cannot do or guarantee. |

Every section is required. When nothing applies, its text is `None.`. Blank lines are allowed anywhere. `parse_skill_file` rejects, with `<path>: body: <msg>`:

- a missing section (`missing section '## Requires'`, naming the first missing one);
- any other heading line (a line starting with 1 to 6 `#` and a space), including `# <name>` titles and `###` subheadings (`unknown heading`);
- a repeated or out-of-order section;
- a non-blank line before the first heading (`text outside a section`);
- an empty section;
- a body longer than `BODY_MAX_CHARS` = 800 characters after stripping (tunable), headings included.

`SkillFile.sections` is a tuple of frozen `SkillSection(heading, text)`, one per heading in template order: `heading` without the `## ` prefix, `text` stripped of surrounding whitespace and otherwise verbatim (inner line breaks kept). The dispatcher renders them without knowing the heading names.

Numbers in the body that mirror a code constant are literal: walk's `0.3 m/s` is `VELOCITY_MPS` and turn's `1.0 rad/s` is `YAW_RATE_RPS`. `test_prose_numbers_match_code_constants` in `skills/tests/unit/test_catalog.py` fails if a body and its constant disagree; a new mirrored number gets a line there.

## Invocation

```
python -m skills.<name> '<params-json>'
```

- `argv[1]` is a JSON object with the **filled** params: checked, normalised and with defaults filled in by the dispatcher. Always present (`{}` for no params).
- Environment, set by the executor through `contract.env.child_env()` (secrets `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` removed by the executor first). `contract/env.py` defines every variable name below; skills and backends read them only through its constants:

| Variable | Meaning |
|---|---|
| `GO2_BACKEND` | `real` or `stub`. Missing or unknown → `backend_not_configured`. |
| `GO2_IFACE` | Network interface (real backend). |
| `GO2_YOLO_WEIGHTS` | Path to the YOLO weights. |
| `GO2_STUB_STATE_FILE` | Stub state file. |
| `GO2_STUB_TIME_SCALE` | Stub time scale. |
| `GO2_STUB_OBJECTS` | JSON `{class: {"x": m, "y": m, "height_m": m}}`, the objects in the stub world ([stub backend](../../skills/docs/skills.md#stub-backend)). |
| `GO2_STUB_FAULT` | Fault kind; set only for the faulted step, never inherited from the dispatcher's environment. |
| `GO2_PARENT_PID` | Dispatcher PID, for the orphan watchdog. |
| `GO2_VISION_SOCKET` | Unix socket of the [vision service](../../skills/docs/skills.md#vision-service); `child_env` sets it to `env.vision_socket_path(parent_pid)`. Read by `detect_object` and the service. |
| `GO2_ROBOT_SOCKET` | Unix socket of the [robot service](../../skills/docs/skills.md#robot-service); `child_env` sets it to `env.robot_socket_path(log_dir)` = `<log_dir>/robot.sock`. Read by every skill and by the service. |
| `GO2_LEASE_TOKEN` | The step's [lease](../../skills/docs/skills.md#lease) token, which the dispatcher armed on its control connection before the step; `child_env(..., lease_token=)` sets it for that step only, never inherited. A motion skill sends it with every primitive; without it the service rejects motion (`motion_rejected`). |
| `PYTHONUNBUFFERED` | `1`. |

- The process runs with the base dir as working directory, in its own session (`start_new_session=True`), so a kill reaches the whole process group ([safety.md](../../docs/safety.md#the-stop-path)).
- **Output:** exactly one JSON line on the real stdout, written by `runner.emit()` through the stdout fd that `skills/process.py` saves. Everything else goes to stderr: `process.capture_stdout()` points fd 1 at stderr first, so output from the SDK, CycloneDDS, ultralytics or C code cannot corrupt the response line. The executor reads the last non-empty stdout line and requires its `skill` to match.
- **Exit code:** 0 for `status = "ok"`, 1 for `status = "error"` (`emit` uses `os._exit`, because DDS threads can hang normal interpreter shutdown). No valid response line means step outcome `malformed`.
- Skills do not validate params: they read them directly (`params["direction"]`) and apply no defaults. The dispatcher validates every plan against the `SKILL.md` types, enums and ranges before it runs a skill ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)). A skill run by hand is therefore not range-limited and needs all params; a missing key or wrong type ends as `exception`. Only `detect_object.target` is trimmed and lower-cased by the skill.

## Response schema

Every skill and utility prints one `SkillResponse` (`schema_version` 2). `SkillResponse`, `SkillError` and `RobotState` are defined once, as frozen standard-library dataclasses in `contract/schema.py`; the dispatcher re-exports them from `dispatcher/models.py`. The response is built by `runner.build_response(skill, outcome, states, total_ms, *, setup)` (`skills/runner.py`) from its four sources and printed with `runner.to_json` (`json.dumps(dataclasses.asdict(...), allow_nan=False)`):

- `outcome`, the frozen `SkillOutcome` the body returns: `status`, `observations`, `error`, and the body's own timing keys. An exception that escapes the body becomes `SkillOutcome.from_exception(e)`: `InvalidParams` maps to `invalid_params`, `BackendNotConfigured` to `backend_not_configured`, anything else to `exception` with message `<ExceptionType>: <first line>` (`runner.describe_exception`) and its traceback on stderr.
- `states`, the `runner.StateSampler` that `run_main` creates before the body runs: `state_before`, `state_after`, `state_error` (the failed samples' `<label>: <ExceptionType>: <first line>`, joined with `; `) and the time spent sampling. `states.take("before" | "after")` samples into that field; `strict=True` re-raises a failed sample instead of recording it. `BackendNotConfigured` always propagates. Because `run_main` owns the sampler, a sample taken before an exception is still reported. By default (skills) the sampler reads through the [robot service](../../skills/docs/skills.md#robot-service) on the process's one connection, `robot_ipc.session`, connecting before the timed read. The utilities pass their own `reader` and `prepare` (`skills/local.py`): a read in the process itself, which on the real backend means its own DDS subscription, after DDS init ([robot.md](../../skills/docs/robot.md#state-sampling)). Its `clock` (default `time.monotonic`) times the reads for `state_ms`; only tests pass another.
- `total_ms`, measured by `run_main` from its start to `emit`.
- `setup`, the process's setup time, read by `emit` from `run_main`'s `setup` callable: in a skill `robot_ipc.session.setup_timing()`, `{"robot_connect_ms": ...}` once the process has connected to the robot service; in a utility `local.setup_timing()`, `{"dds_init_ms": ...}` once it has attempted DDS init ([robot.md](../../skills/docs/robot.md#sdk-and-dds-usage)). Empty when the process did not get that far.

`timing` is the setup key, then the outcome's keys, then `state_ms` if a sample was attempted, then `total_ms`. `run_main(skill, body, *, watchdog=True, states=None, setup=robot_ipc.session.setup_timing)` runs `body(states)` and emits; `run_skill(policy, body, *, sample_state=True)` wraps it for skills (parse params, sample before, `body(params)`, sample after). The utilities pass their own body, sampler and setup to `run_main`: `stop_move` returns its `invalid_params` error after `StopMove()` so `sdk_ret` and its timing are kept, and `read_state` samples strictly and maps a failure to `state_unavailable`. Neither needs the robot service: `stop_move` must work when nothing else does, and `read_state` is the diagnosis read that bypasses the service.

Constructing a `SkillResponse` checks only that `error` is present if and only if `status = "error"`. `build_response` also raises `ValueError` for `status = "error"` without `error_code` or `error_message`; `emit` turns any exception while building or serialising the line into an `exception` error line without the body's observations and states. The dispatcher validates the whole line against the same classes with a pydantic `TypeAdapter`: types, enums, and no unknown keys at any level, including inside `error` and the state objects. A line that fails it is step outcome `malformed`.

| Field | Meaning |
|---|---|
| `schema_version` | Always `2`. |
| `skill` | Skill name; must match the skill that was run. |
| `status` | `ok` or `error`. |
| `observations` | Skill-specific results. |
| `error` | `{code, message}` if and only if `status = "error"`, else `null`. `message` is collapsed to one line (whitespace runs become one space) and not length-limited; the dispatcher cuts it where it shows it ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message)). |
| `state_before`, `state_after` | Robot state sampled at start and end ([robot.md](../../skills/docs/robot.md#state-sampling)), each with the `seq` of the message it was read from and its `age_ms` at the read; utilities have `state_after` only. |
| `state_error` | Why a state sample failed (`before: ...` / `after: ...`, joined with `; `), else `null`. |
| `timing` | Milliseconds, each part measured once. `robot_connect_ms` (skills): the connect and `hello` to the robot service, summed over the process's connects. `dds_init_ms` (utilities): DDS init, once per process. `client_init_ms` (`stop_move`): sport client creation and `Init()`, DDS init excluded. `connect_ms`: `detect_object`'s connect to the vision service. `exec_ms`: the skill's action; for `walk` and `turn` the `move` request until its reply, for `sit` and `stretch` the `action` request plus the settle wait. `stop_call_ms` (`stop_move`): the `StopMove()` call until it returns. `state_ms`: time in the state samples, connect and DDS init excluded; in a skill, the round trips to the robot service; in a utility, its own read. `total_ms`: the whole process from `run_main` start. The setup key is present only if the process got to that setup, `state_ms` only if a state sample was attempted (both absent, for example, when a skill's params are invalid). |

**Reading records without `dds_init_ms`.** Run logs whose timing has no `dds_init_ms` key used other timing meanings: `init_ms` is the sport client creation and `Init()` (`client_init_ms`), or the vision connect in `detect_object` (`connect_ms`); `state_ms` includes DDS init, which ran inside the first state sample; and `stop_call_ms` runs from utility start to the return of `StopMove()`, so it includes DDS init and client creation. Compare such records with current ones only by these definitions.

**Reading skill records with `client_init_ms`.** A skill whose timing has `client_init_ms` created its own DDS participant and sport client and sent `Move` itself: its timing has `dds_init_ms` and `client_init_ms` instead of `robot_connect_ms`, and its `state_ms` includes the robot service connect. Compare such records with current ones only by these definitions.

**Reading records with `schema_version` 1.** Skills read state in their own process with their own DDS subscription: a skill's `state_ms` includes the subscription, DDS discovery and the wait for the first message (DDS init is in `dds_init_ms` when present), and every skill, `detect_object` included, reports `dds_init_ms`. The states have no `seq` and `age_ms`.

Robot state fields, their sources and the posture rule are in [robot.md](../../skills/docs/robot.md#state-sampling). The dispatcher checks `requires` against a fresh state read before each step and uses `state_before` and `state_after` for fault detection on every dispatched step and a verdict on each step with outcome `ok` ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#verification)); what the model sees of the state and of a detected fault is in [robot.md](../../skills/docs/robot.md#state-sampling) and [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message).

Run a skill by hand ([skills.md](../../skills/docs/skills.md#running-a-skill-by-hand)) to see a full response.

## Error codes

The codes are the members of `ErrorCode` in `contract/schema.py`, a `str` enum: skills pass members, the response line carries the plain string, and `SkillError.code` stays a `str`.

| Code | Skills | Meaning |
|---|---|---|
| `invalid_params` | all | The params argument is missing, not valid JSON, or not a JSON object. Keys and values are not checked by the skill (the dispatcher validates them). |
| `sdk_error` | walk, turn, sit, stretch, stop_move | An SDK call returned non-zero. Message `<Call> returned <code>` (walk/turn add `; StopMove returned <code>` if the cleanup stop also failed). The service sends `StopMove` after a failed `Move`. Whether the robot is left moving is not read from this code: the service ends the lease with another `StopMove`, and the `disarm` reply reports the lease's latest one if it failed ([Lease](../../skills/docs/skills.md#lease)). |
| `robot_unavailable` | walk, turn, sit, stretch | The [robot service](../../skills/docs/skills.md#robot-service) could not be used for the primitive: `ServiceUnavailable: <message>` (nothing listening, connection lost, no reply in time, malformed reply). |
| `motion_rejected` | walk, turn, sit, stretch | The service refused the primitive or ended it: `no_lease: <message>` (no lease armed, wrong or missing `GO2_LEASE_TOKEN`, another connection holds the lease, deadline passed) or `aborted: the lease ended: <reason>` (stop, disarm, re-arm or deadline while it ran; the robot was stopped). |
| `backend_not_configured` | all | `GO2_BACKEND` missing or unknown, or `GO2_IFACE` empty on the real backend. |
| `exception` | all | Unexpected exception: `<Type>: <first line>`. The traceback goes to stderr. Also the robot service's `sdk_exception`, when its sport client raised (it then sent `StopMove`). |
| `unsupported_object` | detect_object | Target is not one of the 80 COCO classes, for example `'phone' is not a detectable object. Closest supported: cell phone, spoon, horse.` (up to 3 close matches, if any). |
| `detector_error` | detect_object | Any other detector failure (`backend.DetectorError`; the specific cases below are its subclasses). |
| `camera_unavailable` | detect_object | The camera returned an error or no data (also the stub's `error` fault). |
| `vision_unavailable` | detect_object | The [vision service](../../skills/docs/skills.md#vision-service) could not be used: `GO2_VISION_SOCKET` unset, nothing listening (`no vision service at <path>: <error>`), the connection lost, no reply within `REPLY_WAIT_S` (`no reply within 5s`), or a malformed reply. |
| `bad_frame` | detect_object | The image could not be decoded. |
| `weights_missing` | detect_object | The YOLO weights file does not exist. |
| `state_unavailable` | read_state | No state could be sampled. |

Detector errors have the message `<ExceptionType>: <message>`; an error the service raised keeps the message the service built, in the same form. Outcomes the dispatcher adds on its own (`timeout`, `malformed`, `rejected`, `motion_budget_exceeded`, `interrupted`) are listed in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-outcomes).

## Policies

Each skill module defines one module-level `POLICY = policy.SkillPolicy(name, timeout, cost, context_observations)` (`contract/policy.py`). `timeout` and `cost` (a `MotionCost(distance_m, rotation_deg)`, default zero) are each a constant or a function of params; `context_observations` lists the observation keys shown to the model on `ok`. The dispatcher imports the module only to read `POLICY`.

The dispatcher reads only `name`, `context_observations`, `timeout_s(params)` and `motion_cost(params)`, with the filled, checked params. Every number is a module-level constant in `skills/<skill>.py`, except the motion timeout rule, which `walk` and `turn` share from `skills/motion.py`:

| Skill | Timeout | Motion cost |
|---|---|---|
| `walk` | `motion_timeout(distance_m / VELOCITY_MPS + LEAD_S)` | `distance_m` |
| `turn` | `motion_timeout(radians(angle_deg) / YAW_RATE_RPS × CAP_FACTOR + CAP_S)` (the cap) | `rotation_deg = angle_deg` (requested, not measured) |
| `sit`, `stretch`, `detect_object` | `TIMEOUT_S` | zero |

`motion.motion_timeout(motion_s)` is `MOTION_BASE_S + MOTION_FACTOR × motion_s` (10.0 s and 1.5): `MOTION_BASE_S = PROCESS_OVERHEAD_S + SPORT_CLIENT_TIMEOUT_S`, where `PROCESS_OVERHEAD_S` (5.0 s, tunable) covers process start, the robot service connect and the two state samples, and `SPORT_CLIENT_TIMEOUT_S` (5.0 s, tunable, `skills/robot_ipc.py`) is one lost `StopMove` reply in the service; `MOTION_FACTOR` (tunable) is a margin on the commanded motion time `motion_s`. Each of `walk` and `turn` computes `motion_s` in one private `_motion_s(params)`, used by both its timeout and its body, and asks for it with `motion.move(Velocity(vx, vy, vyaw), duration_s, target=None)` (turn passes a `MoveTarget`, and `motion_s` is its cap), which the service runs at `robot_ipc.CMD_PERIOD_S` (0.1 s, 10 Hz); `detect_object`'s `TIMEOUT_S` is `PROCESS_OVERHEAD_S + REPLY_WAIT_S` (10.0 s), where `REPLY_WAIT_S` (5.0 s, tunable) is its wait for the vision service's reply: one camera frame (`CAMERA_TIMEOUT_S`, 3.0 s) plus inference on a detector the service has already loaded, so a service that does not answer ends the call as `vision_unavailable` before the dispatcher kills it. The timeout covers the whole process from start to exit, including any [settle wait](../../skills/docs/robot.md#settle-waits). Every process whose call reaches the sport client (`walk`, `turn`, `sit`, `stretch` through the service, `stop_move` directly) has a timeout of at least `PROCESS_OVERHEAD_S + SPORT_CLIENT_TIMEOUT_S` plus its motion or settle time at max params. Each primitive makes at most one reply-waiting call (`StopMove`, `StandDown` or `Stretch`; `Move` sends without a reply), and a skill waits for the primitive's reply for its duration plus `SPORT_CLIENT_TIMEOUT_S` plus `REPLY_WAIT_S` (2.0 s), so a service that does not answer ends the call as `robot_unavailable` before the dispatcher kills it. `tests/integration/test_skill_policies.py` enforces this. The motion cost feeds the motion budget ([safety.md](../../docs/safety.md#motion-budget)).

The utilities `stop_move` and `read_state` export a `POLICY` too, so every module the dispatcher runs carries its name and timeout the same way: `SkillPolicy(name, timeout=TIMEOUT_S)` with `TIMEOUT_S` 12.0 s for `stop_move` (one lost `StopMove` reply plus its settle wait, tunable) and 10.0 s for `read_state`, zero motion cost and no `context_observations` (the defaults). They have no `SKILL.md`, so the registry never loads them; their `POLICY` is never in the catalog or the registry hash.

## Wire framing

`contract/wire.py` holds what the [vision service](../../skills/docs/skills.md#vision-service) and [robot service](../../skills/docs/skills.md#robot-service) protocols share: the socket family `FAMILY` (`AF_UNIX`), the connection type `Conn`, and `encode(message)` / `decode(data)`. A message is one JSON object as UTF-8 bytes (compact separators, no NaN), sent with `multiprocessing.connection`'s `send_bytes`, never pickle; `decode` raises `ValueError` for anything that is not a JSON object. The operations, message fields and size limits of each protocol are in [skills.md](../../skills/docs/skills.md).

## Shared constants

`contract/constants.py` holds the domain constants and small types that more than one package reads:

| Name | Value | Read by |
|---|---|---|
| `YAW_SIGN` | `{"left": 1, "right": -1}`: a turn direction to the sign of `yaw_rad` and `vyaw` (counter-clockwise +) | `turn` (the command), `contract.manifest` (`yaw_delta` validation), the dispatcher's verification |
| `Link` | `"up"` \| `"down"`: whether robot state messages are arriving ([robot.md](../../skills/docs/robot.md#state-sampling)) | the state store and the robot service protocol, the dispatcher's run log and transports |

## Design decisions

- **One package, `contract`, holds what the dispatcher, the skills and the services agree on, and imports none of them.** Each side then depends on the contract instead of on the other side's implementation modules. Its modules are standard library only (the one exception is `manifest`, below), because skill processes import them at every spawn.
- **One module per contract: `manifest`, `env`, `schema`, `policy`, `wire`, `constants`.** The shared framing is named `wire`, not `ipc`, so it is not taken for a protocol client (`robot_ipc`, `vision_ipc`), which stay with the services they speak to. `constants` holds a value only when a second package would otherwise import a service, client, backend or stub module just for it: `YAW_SIGN` and `Link`. Rejected: `YAW_SIGN` in the robot service protocol module and `Link` in the state store; the manifest schema and the dispatcher would import those modules only for the one value. Values one package uses, and values a package reads from a module it uses anyway (the protocol limits, the service lifecycle constants, the stub's fault kinds and defaults), stay where they are.
- **One common JSON response schema for all skills**, replacing the predecessor's three incompatible output formats, so the executor, the log and the context renderer handle every skill the same way.
- **Each skill owns its policy** (timeout formula, motion cost, observations shown to the model) as module constants next to the code that shares them, so a number like walking speed is defined once and tuned in one place.
- **A policy is one frozen `SkillPolicy` dataclass instance per skill, not a subclass per skill.** The skills differ only in data (a name, a constant or a small formula for timeout and cost, an observation list), so a value with optional callables says it in a few lines and the constants sit at module level where `run_skill(POLICY, body)` and the body use them without a class prefix. The dataclass lives in `contract/policy.py`, which imports only the standard library, so the dispatcher's own modules import the type without the runner. Rejected: a `SkillPolicy` base class with one `<Name>Policy` subclass per skill overriding `timeout_s()`/`motion_cost()`; it repeated boilerplate in every module and spread the constants across class attributes, without adding behaviour. The dispatcher still calls `timeout_s(params)` and `motion_cost(params)`, so it does not need to know whether a value is constant.
- **Utilities carry a `POLICY` like skills, as a plain `SkillPolicy` with the default cost and observations.** One mechanism for every module the dispatcher spawns, so the dispatcher reads a utility's name and timeout from its module instead of hard-coding them. The defaults fit both utilities (zero cost: neither commands motion), so a smaller utility-only type would add a second type without a field to drop that matters. Rejected: a `UTILITIES` map in `skills` (a second registry of names and timeouts beside the modules that own them); keeping utility timeouts as dispatcher config keys (`robot.*_timeout_s`), which gave timeouts two sources of truth, config for utilities and `POLICY` for skills.
- **Param and response validation happen on the dispatcher side; the response schema is defined once in `contract`.** Plan params are validated only by the dispatcher's plan and bounds validation against `SKILL.md`; skills read params directly. Skill responses are fully validated only by the dispatcher, but against the dataclasses in `contract/schema.py`, so the skill side constructs the very classes the dispatcher checks and there is no second definition to drift. The one rule that runs in both processes is the error-iff-status check in `SkillResponse.__post_init__`: it is part of the class, costs nothing, and pydantic reports its `ValueError` as a validation error. Rejected: separate skill-side validation of params or response types; the skill-side param checks only mattered for hand-run skills, where a missing key now reports `exception` instead of `invalid_params`.
- **The response schema is standard-library frozen dataclasses (`contract/schema.py`), validated by pydantic only in the dispatcher.** Skill processes must start fast and never import pydantic ([skills.md](../../skills/docs/skills.md#no-side-effects-on-import)), yet the shape must be defined once. pydantic validates dataclasses directly; `__pydantic_config__ = {"extra": "forbid"}` is a plain dict, so `schema.py` needs no pydantic import. `RobotState` forbids extra keys like the other two: `asdict` and pydantic's dump would silently drop them, so a new robot field must be added to the class. A direct constructor call does no type checks, only `__post_init__`; type checks happen when the dispatcher parses the line. Rejected: pydantic models in `contract` (about 80 ms added to every spawn, `stop_move` included); dispatcher-only models plus a test that keeps the skill-side dicts in step (still two definitions); a JSON Schema file (needs code generation or a new dependency); `TypedDict` (pydantic needs `typing_extensions.TypedDict` on Python < 3.12); msgspec (a C dependency in the skill processes).
- **The `SKILL.md` schema and parser live in `contract/manifest.py`, the one `contract` module that imports pydantic and PyYAML.** The manifest schema is the contract between the skill files and the dispatcher's registry, so it sits with the rest of the contract, and its rules are documented and tested here. pydantic is acceptable there because only the dispatcher process imports the parser; skill subprocesses never do, which an import-linter contract enforces for imports through the repo's own modules ([testing.md](../../tests/docs/testing.md#import-boundaries)). Every other `contract` module stays standard library only, because skill processes import them at every spawn. The registry hash stays in the dispatcher, because it also covers the system text and tool schema. Rejected: the parser in `dispatcher/` with only standard-library types in `contract/`; the skill tests also validate `SKILL.md` files, and the manifest schema is the contract, so it would sit in the package that only consumes it.
- **Expectations and preconditions are typed `SKILL.md` frontmatter that the catalog does not render** (user decision). They describe the skill, so they sit in its manifest next to its params and are validated when the registry loads; leaving them out of the catalog keeps the prompt surface, the golden file and the registry hash unchanged, and the model needs neither to plan (preconditions already reach it through the body's `Requires` section). This is not the rejected frontmatter-only `requires:` below: that one would have replaced the model-facing prose, while this one is a machine check beside it. Posture is the only state term, because the posture rule already holds the body-height thresholds; a separate body-height bound would copy them into every `SKILL.md`. Rejected: rendering them into the catalog (changes the hash and the study's prompt for no planning gain); a table in the dispatcher keyed by skill name (a skill's contract would be split across two packages and could drift from its `SKILL.md`).
- **The `SKILL.md` body is model-facing, thin, and in a fixed section template that the parser enforces** (user decision). The planner lacked contract facts it needs to plan (posture preconditions, speeds), and those facts belong next to the skill they describe. The body holds only what the planner needs; human documentation is on this page and in [robot.md](../../skills/docs/robot.md). The same four headings for every skill and every skill set (granularity tier) keep tiers comparable: what differs between tiers is the skills, not how well or how much each one is described. All sections are required, with `None.` when nothing applies, so every skill has the same shape and a forgotten section is an error rather than a silent gap. The length cap stops bodies growing back into documentation. Mirrored numbers stay literal, guarded by a test, so the model reads plain numbers and the code stays the single source of truth for them. Rejected: frontmatter-only fields (`requires:`, `limits:` keys), because a skill is then just a typed function signature, not really a skill; free-form prose, because its length and quality would vary per skill and tier and confound the granularity comparison; Agent Skills style on-demand loading (the model reads a body only when it asks), because it adds a round-trip per skill and makes the context differ between calls.
- **`contract` owns the process environment contract, the posture type and the error codes; the dispatcher and the skills import them.** These are contracts between the executor and the skill processes. Defining them once (`contract/env.py`, `schema.Posture`, `schema.ErrorCode`) removes the copies in the executor and config that could drift silently: a renamed variable or a new error code would otherwise need matching edits in both packages. `ErrorCode` is `class ErrorCode(str, Enum)` with `__str__` returning the value, because Python 3.10 has no `StrEnum`; the members serialise and compare as their plain strings, so the response line and the dispatcher's validation are unchanged. `SkillError.code` stays `str`, so pydantic does not reject a code the dispatcher does not know. `Posture` lives in `schema.py` next to `RobotState`, which uses it, rather than in `backend.py`. Rejected: a dispatcher-owned env builder (it would keep the variable names in the package that only sets them); typing `SkillError.code` as `ErrorCode` (an unknown code would turn a skill error into `malformed`).
- **The response is built from four sources, `SkillOutcome` + `StateSampler` + `total_ms` + `setup`, each written by the code that owns it.** The body owns the outcome, the sampler owns the state fields and their timing, the connection owner owns the setup time (`robot_ipc.session` in a skill, `skills/local.py`'s DDS init in a utility), and `run_main` owns the total time and the exception mapping (`SkillOutcome.from_exception`). `run_main` creates the sampler before calling the body, so samples taken before an exception survive without the body writing into a shared result. Accepted edge case: any exception in `stop_move` after `StopMove()` returns (the settle `backend.sleep` on a malformed `GO2_STUB_TIME_SCALE`, or the after-sample raising `BackendNotConfigured`) drops `sdk_ret`, `client_init_ms` and `stop_call_ms` from the response; only misconfiguration reaches it. Rejected: a mutable `ResponseDraft` (a typed version of the old `out` dict: three writers still mutate one object, and it duplicates `SkillResponse`); plain locals in `run_main` with `try/finally` (it either duplicates the exception mapping in each utility or keeps utilities mutating a result argument).
- **Setup cost is its own field (`robot_connect_ms` in a skill, `dds_init_ms` and `client_init_ms` in `stop_move`); `state_ms` measures sampling only and `stop_call_ms` the `StopMove()` call only.** Per-process DDS and sport client setup costs about 1 s on the robot; `stop_move` pays it, a skill pays a socket connect instead. One setup field, or a `state_ms` that includes DDS init, cannot tell DDS from sport client cost, nor sampling from setup. Each field measures one part, so per-step setup can be attributed and kill-to-stop latency is the sum of its parts ([safety.md](../../docs/safety.md#measured-latency)). `dds_init_ms` includes the import of the SDK channel module, because that import loads CycloneDDS and no other field would count it. Rejected: keeping `stop_call_ms` as the whole stop from utility start, because it would again mix DDS, client and call time; the whole stop is `stop_move.duration_ms` in the run log.
- **Skills collapse an error message to one line but do not limit its length; the dispatcher owns the limit.** One limit in one place, so a message is never cut twice to two different lengths, and the run log keeps the skill's full message in `response`. Rejected: a skill-side cap as well (it was 300 characters against the dispatcher's 200, so the skill cut was dead for every view except the logged response).
- **No `integer` param type.** No skill used it; a whole-number parameter is declared `number`. Full decision in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md).
