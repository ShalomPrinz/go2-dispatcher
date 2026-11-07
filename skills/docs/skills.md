# Skills

The skill contract: how a skill is declared, invoked and answers, how the catalog the model sees is generated, the stub backend and fault injection, and how to add a skill. Robot-side behaviour (SDK calls, real backend, posture rule, settle waits) is in [robot.md](robot.md).

A skill has two parts:

1. `skills/catalog/<name>/SKILL.md`: YAML frontmatter that the registry reads, plus a short model-facing body in a fixed section template ([below](#skillmd-body)). This file is the skill's **manifest**: frontmatter plus body sections. Nothing in a `SKILL.md` is for humans; human documentation lives in this page and [robot.md](robot.md).
2. `skills/<name>.py`: a module with a `main()` that runs as a subprocess, and a module-level `POLICY` object that the dispatcher reads.

v1 skills: `walk`, `turn`, `sit`, `stretch`, `detect_object`. Two utilities, `stop_move` and `read_state`, run the same way but are not skills: they have no `SKILL.md` and the model never sees them. The registry loads every subfolder of `skills.dir` that has a `SKILL.md`, so a different skill set (for example another granularity tier) is a different folder ([running.md](../../docs/running.md)).

## SKILL.md frontmatter

The YAML block between the first two lines that are exactly `---` (the file must start with `---`), parsed with `yaml.safe_load`. The schema (`ParamSpec`, `SkillFrontmatter`, `SkillSection`, `SkillFile`) and the parser (`parse_skill_file`) live in `skills/manifest.py`; the dispatcher's registry calls them and adds the checks that need the folder or the module. `parse_skill_file` validates the frontmatter, then the [body](#skillmd-body), and returns a `SkillFile`: the `SkillFrontmatter` keys plus `sections`.

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

Any other key (`sections` included), a missing required key or an invalid value is a registry error, as is a body that breaks the [template](#skillmd-body), and so are: an entrypoint that cannot be imported, a module without `POLICY`, a `POLICY` that is not a `SkillPolicy`, `POLICY.name` ≠ `name`, a missing skills folder, or zero skills. Transports print `Registry error: <message naming the file>` and exit 2. A frontmatter error reads `<path>: <dotted.loc>: <msg>` (e.g. `params.distance_m.min`); an extra key reads `unknown key`; a body error reads `<path>: body: <msg>`. The parser raises these as `SkillFileError`, and the registry re-raises them unchanged as registry errors.

`skills/catalog/walk/SKILL.md` is a full example. The dispatcher checks every planned step against these declarations before anything runs ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)).

## SKILL.md body

Everything after the closing `---` is the body: text for the model, written for the planner. It holds only what the planner needs to choose calls and params: preconditions (such as failing while sitting), effects and limits (such as speed or the COCO-class restriction). No SDK calls, rates, environment variables, settle constants or run commands; those are here and in [robot.md](robot.md). Params, ranges and the one-line `description` are already in the frontmatter and are not repeated.

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

Numbers in the body that mirror a code constant are literal: walk's `0.3 m/s` is `VELOCITY_MPS` and turn's `1.0 rad/s` is `YAW_RATE_RPS`. `test_prose_numbers_match_code_constants` in `skills/tests/unit/test_manifest.py` fails if a body and its constant disagree; a new mirrored number gets a line there.

## Catalog and registry hash

The catalog (`Registry.catalog_text()`) is generated from the frontmatter and the body sections, skills sorted by name, one line per parameter, no trailing newline. The body sections are sent to the model in full, rendered after the parameter lines ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md) has the format). The catalog is sent as `system[1]` = `"## Skills\n" + catalog`. The current catalog is the golden file `dispatcher/tests/golden/catalog.txt`.

Type phrases: `one of a, b`; `text`; `number from X to Y`, `number, at least X`, `number, at most Y` or `number`, followed by the unit.

The **registry hash** is the first 16 hex characters of SHA-256 over `system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True)`. It identifies the whole prompt surface, so it changes with the skill set, any `SKILL.md` frontmatter and body wording, the system text and the horizon (which appears in the system text and the tool schema). It is logged in `task_start` and `index.jsonl`; runs with different hashes are not directly comparable. `go2 catalog` prints the system text, catalog, tool schema and hash.

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
| `GO2_STUB_OBJECTS` | JSON `{class: {"x": m, "y": m, "height_m": m}}`, the objects in the stub world ([stub backend](#stub-backend)). |
| `GO2_STUB_FAULT` | Fault kind; set only for the faulted step, never inherited from the dispatcher's environment. |
| `GO2_PARENT_PID` | Dispatcher PID, for the orphan watchdog. |
| `GO2_VISION_SOCKET` | Unix socket of the [vision service](#vision-service); `child_env` sets it to `env.vision_socket_path(parent_pid)`. Read by `detect_object` and the service. |
| `PYTHONUNBUFFERED` | `1`. |

- The process runs with the base dir as working directory, in its own session (`start_new_session=True`), so a kill reaches the whole process group ([safety.md](../../docs/safety.md#the-stop-path)).
- **Output:** exactly one JSON line on the real stdout, written by `runner.emit()` through the stdout fd that `skills/process.py` saves. Everything else goes to stderr: `process.capture_stdout()` points fd 1 at stderr first, so output from the SDK, CycloneDDS, ultralytics or C code cannot corrupt the response line. The executor reads the last non-empty stdout line and requires its `skill` to match.
- **Exit code:** 0 for `status = "ok"`, 1 for `status = "error"` (`emit` uses `os._exit`, because DDS threads can hang normal interpreter shutdown). No valid response line means step outcome `malformed`.
- Skills do not validate params: they read them directly (`params["direction"]`) and apply no defaults. The dispatcher validates every plan against the `SKILL.md` types, enums and ranges before it runs a skill ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-bounds)). A skill run by hand is therefore not range-limited and needs all params; a missing key or wrong type ends as `exception`. Only `detect_object.target` is trimmed and lower-cased by the skill.

## Response schema

Every skill and utility prints one `SkillResponse` (`schema_version` 1). `SkillResponse`, `SkillError` and `RobotState` are defined once, as frozen standard-library dataclasses in `skills/schema.py`; the dispatcher re-exports them from `dispatcher/models.py`. The response is built by `runner.build_response(skill, outcome, states, total_ms, *, dds_init_ms)` (`skills/runner.py`) from its four sources and printed with `runner.to_json` (`json.dumps(dataclasses.asdict(...), allow_nan=False)`):

- `outcome`, the frozen `SkillOutcome` the body returns: `status`, `observations`, `error`, and the body's own timing keys. An exception that escapes the body becomes `SkillOutcome.from_exception(e)`: `InvalidParams` maps to `invalid_params`, `BackendNotConfigured` to `backend_not_configured`, anything else to `exception` with message `<ExceptionType>: <first line>` (`runner.describe_exception`) and its traceback on stderr.
- `states`, the `runner.StateSampler` that `run_main` creates before the body runs: `state_before`, `state_after`, `state_error` (the failed samples' `<label>: <ExceptionType>: <first line>`, joined with `; `) and the time spent sampling. `states.take("before" | "after")` samples into that field; `strict=True` re-raises a failed sample instead of recording it. `BackendNotConfigured` always propagates. Because `run_main` owns the sampler, a sample taken before an exception is still reported.
- `total_ms`, measured by `run_main` from its start to `emit`.
- `dds_init_ms`, the time the process spent in DDS init, read by `emit` from the backend (`backend.dds_init_ms()`, [robot.md](robot.md#sdk-and-dds-usage)); `None` when the process did not init DDS, and then absent from `timing`.

`timing` is `dds_init_ms` if DDS was initialised, then the outcome's keys, then `state_ms` if a sample was attempted, then `total_ms`. `run_main(skill, body, *, watchdog=True)` runs `body(states)` and emits; `run_skill(policy, body, *, sample_state=True)` wraps it for skills (parse params, sample before, `body(params)`, sample after). The utilities pass their own body to `run_main`: `stop_move` returns its `invalid_params` error after `StopMove()` so `sdk_ret` and its timing are kept, and `read_state` samples strictly and maps a failure to `state_unavailable`.

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
| `timing` | Milliseconds, each part measured once. `dds_init_ms`: DDS init, once per process, wherever it first happens (the first state sample, or the sport client in `stop_move`). `client_init_ms`: sport client creation and `Init()`, DDS init excluded (skills that call the sport client, and `stop_move`). `connect_ms`: `detect_object`'s connect to the vision service. `exec_ms`: the skill's action. `stop_call_ms` (`stop_move`): the `StopMove()` call until it returns. `state_ms`: time in the state samples, DDS init excluded. `total_ms`: the whole process from `run_main` start. `dds_init_ms` is present only if the process attempted DDS init, `state_ms` only if a state sample was attempted (both absent, for example, when a skill's params are invalid). |

**Reading records without `dds_init_ms`.** Run logs whose timing has no `dds_init_ms` key used other timing meanings: `init_ms` is the sport client creation and `Init()` (`client_init_ms`), or the vision connect in `detect_object` (`connect_ms`); `state_ms` includes DDS init, which ran inside the first state sample; and `stop_call_ms` runs from utility start to the return of `StopMove()`, so it includes DDS init and client creation. Compare such records with current ones only by these definitions.

Robot state fields, their sources and the posture rule are in [robot.md](robot.md#state-sampling). State is **logged only**; the model sees only the derived posture ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message)).

Run a skill by hand ([below](#running-a-skill-by-hand)) to see a full response.

## The skills

| Skill | Action | Observations | Shown to the model on `ok` |
|---|---|---|---|
| `walk` | `Move` for `distance_m / VELOCITY_MPS + LEAD_S` s, then `StopMove` | `direction`, `distance_m`, `duration_s` (commanded), `sdk_ret`, `orphaned` (only if set) | nothing |
| `turn` | `Move(0, 0, ±YAW_RATE_RPS)` for `radians(angle_deg) / YAW_RATE_RPS + LEAD_S` s, then `StopMove` | `direction`, `angle_deg`, `duration_s`, `sdk_ret`, `orphaned` | nothing |
| `sit` | `StandDown()`, then a settle wait | `sdk_ret` | nothing |
| `stretch` | `Stretch()`, then a settle wait | `sdk_ret` | nothing |
| `detect_object` | asks the [vision service](#vision-service) for one camera frame + YOLO; never moves | `target`, `object_found`, `position` (left/center/right), `closeness` (near/medium/far), `confidence` | `object_found`, `position`, `closeness`, `confidence` |

Params and ranges are in each `SKILL.md`. Walk and turn are open-loop ([robot.md](robot.md#motion)); walk's `left` and `right` are sideways steps that keep the heading. `walk`, `turn` and `stretch` need the robot standing: while sitting, `Move` and `Stretch` return non-zero and the skill reports `sdk_error` (stub behaviour, [below](#stub-backend); *unverified* on the robot). `sit` while already sitting is `ok`. `detect_object` reports the most confident box of the target class; the target must be one of the 80 COCO classes in `skills/coco.py`. Not finding an object is `status = ok` with `object_found: false`, not an error.

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
| `vision_unavailable` | detect_object | The [vision service](#vision-service) could not be used: `GO2_VISION_SOCKET` unset, nothing listening (`no vision service at <path>: <error>`), the connection lost, no reply within `REPLY_WAIT_S` (`no reply within 5s`), or a malformed reply. |
| `bad_frame` | detect_object | The image could not be decoded. |
| `weights_missing` | detect_object | The YOLO weights file does not exist. |
| `state_unavailable` | read_state | No state could be sampled. |

Detector errors have the message `<ExceptionType>: <message>`; an error the service raised keeps the message the service built, in the same form. Outcomes the dispatcher adds on its own (`timeout`, `malformed`, `rejected`, `motion_budget_exceeded`, `interrupted`) are listed in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#step-outcomes).

## Policies

Each skill module defines one module-level `POLICY = policy.SkillPolicy(name, timeout, cost, context_observations)` (`skills/policy.py`). `timeout` and `cost` (a `MotionCost(distance_m, rotation_deg)`, default zero) are each a constant or a function of params; `context_observations` lists the observation keys shown to the model on `ok`. The dispatcher imports the module only to read `POLICY`.

The dispatcher reads only `name`, `context_observations`, `timeout_s(params)` and `motion_cost(params)`, with the filled, checked params. Every number is a module-level constant in `skills/<skill>.py`, except the move-loop timeout rule, which `walk` and `turn` share from `skills/motion.py`:

| Skill | Timeout | Motion cost |
|---|---|---|
| `walk` | `motion_timeout(distance_m / VELOCITY_MPS + LEAD_S)` | `distance_m` |
| `turn` | `motion_timeout(radians(angle_deg) / YAW_RATE_RPS + LEAD_S)` | `rotation_deg = angle_deg` |
| `sit`, `stretch`, `detect_object` | `TIMEOUT_S` | zero |

`motion.motion_timeout(motion_s)` is `MOTION_BASE_S + MOTION_FACTOR × motion_s` (10.0 s and 1.5): `MOTION_BASE_S = PROCESS_OVERHEAD_S + SPORT_CLIENT_TIMEOUT_S`, where `PROCESS_OVERHEAD_S` (5.0 s, tunable) covers process start, SDK init and the two state samples, and `SPORT_CLIENT_TIMEOUT_S` (5.0 s, tunable, `skills/real.py`) is one lost `StopMove` reply; `MOTION_FACTOR` (tunable) is a margin on the commanded motion time `motion_s`. Each of `walk` and `turn` computes `motion_s` in one private `_motion_s(params)`, used by both its timeout and its body, and commands it through `motion.move_loop(Velocity(vx, vy, vyaw), duration_s)` at the shared period `motion.CMD_PERIOD_S` (0.1 s, 10 Hz); `detect_object`'s `TIMEOUT_S` is `PROCESS_OVERHEAD_S + REPLY_WAIT_S` (10.0 s), where `REPLY_WAIT_S` (5.0 s, tunable) is its wait for the vision service's reply: one camera frame (`CAMERA_TIMEOUT_S`, 3.0 s) plus inference on a detector the service has already loaded, so a service that does not answer ends the call as `vision_unavailable` before the dispatcher kills it. The timeout covers the whole process from start to exit, including any [settle wait](robot.md#settle-waits). Every process that calls the sport client (`walk`, `turn`, `sit`, `stretch`, `stop_move`) has a timeout of at least `PROCESS_OVERHEAD_S + SPORT_CLIENT_TIMEOUT_S` plus its motion or settle time at max params, so a reply that never arrives ends the call as `sdk_error` before the dispatcher kills it. Each makes at most one reply-waiting call (`StopMove`, `StandDown` or `Stretch`; `Move` sends without a reply). `tests/integration/test_skill_policies.py` enforces this. The motion cost feeds the motion budget ([safety.md](../../docs/safety.md#motion-budget)).

The utilities `stop_move` and `read_state` export a `POLICY` too, so every module the dispatcher runs carries its name and timeout the same way: `SkillPolicy(name, timeout=TIMEOUT_S)` with `TIMEOUT_S` 12.0 s for `stop_move` (one lost `StopMove` reply plus its settle wait, tunable) and 10.0 s for `read_state`, zero motion cost and no `context_observations` (the defaults). They have no `SKILL.md`, so the registry never loads them; their `POLICY` is never in the catalog or the registry hash.

## No side effects on import

Importing a skill module must do nothing: no SDK import, no DDS init, no argument parsing, no output. All work happens in `main()`, under `if __name__ == "__main__": main()`. At top level a skill imports only the standard library and `skills`. Third-party imports happen inside functions: `unitree_sdk2py` in `skills/real.py`, and `cv2`, `ultralytics` and `numpy` only in `skills/real_detector.py`, which only the [vision service](#vision-service) imports, so no skill process loads torch. A test imports every `skills` module in a fresh interpreter and checks that none of these were loaded. This is what lets the dispatcher read policies without touching the SDK. `skills` never imports `dispatcher`, pydantic or PyYAML, directly or indirectly; the one exception is `skills/manifest.py`, which imports pydantic and PyYAML at top level and is imported only by the dispatcher and the skill tests, never by a skill.

The import-linter contracts enforce these rules before every commit and in CI for every import chain through the repo's own modules, including imports inside functions ([testing.md](../../tests/docs/testing.md#import-boundaries)). They treat third-party packages as leaves, so a third-party import that pulls in pydantic or PyYAML (`import anthropic`, at top level or inside a function) passes them, and the fresh-interpreter test checks only the modules it lists; code review is what enforces the import rule above for such imports. That gap is accepted because the rule exists to keep skill spawn time low and already forbids the import.

## Stub backend

The stub (`skills/stub.py`) replaces only the SDK layer inside the skill process; processes, timeouts, kills and `StopMove` run for real. It simulates only what the real backend gets from the robot (motion, the camera image, the state message) and reuses the rest, such as the detector's box classification. Constants are in `skills/stub.py`.

It keeps posture and an odometry pose in one JSON state file, `{"posture": "standing" | "sitting", "pose": {"x": m, "y": m, "yaw": rad}}`. The pose is in the world frame: the robot starts at (0, 0) facing +x, and yaw is counter-clockwise. A missing file means standing at the origin; a missing `pose` means the origin. Every writer reads the file, changes its own field and replaces the file atomically (temp file + `os.replace`); no lock is needed because only one skill process runs at a time.

| Call | While standing | While sitting |
|---|---|---|
| `Move` | 0 | 1 (not standing) |
| `StopMove` | 0 | 0 |
| `StandDown` | 0; posture → sitting; waits `STAND_DOWN_S` | 0; no change |
| `Stretch` | 0; waits `STRETCH_S` | 1 |

- **Odometry.** An accepted `Move(vx, vy, vyaw)` (return 0) stores the command and `StopMove()` clears it. While a command is stored, every `sleep(seconds)` advances the pose by the **unscaled** seconds, integrating the body-frame velocity exactly along the arc, and writes it to the state file. Body frame as on the Go2: +vx forward, +vy left, +vyaw counter-clockwise (left). The stub does not model the start ramp, so walk and turn overshoot on the stub by their lead time ([robot.md](robot.md#motion)).
- **Camera.** `StubDetector`, served by the [vision service](#vision-service) with the service's own env: `detect(target)` waits `DETECT_S`, then projects the object listed under `target` in `GO2_STUB_OBJECTS` through a pinhole camera `CAMERA_OFFSET_M` ahead of the body centre (`CAMERA_WIDTH_PX` × `CAMERA_HEIGHT_PX`, horizontal field of view `CAMERA_HFOV_DEG`, square pixels; [robot.md](robot.md#object-detection)). The box centre-x fraction is `0.5 − f × tan(bearing) / width`, so a positive bearing (left) gives a small x; the box height fraction is `f × height_m / depth / height`, clipped to 1, where depth is the distance along the camera axis. The box goes to the real detector's classifier, `vision.classify_box`. Not found when the target is not listed, is behind the camera, is outside the horizontal field of view, or its box height fraction is below `MIN_BOX_H`. Confidence is deterministic: `CONFIDENCE_MIN` + (`CONFIDENCE_MAX` − `CONFIDENCE_MIN`) × box height fraction.
- **Objects.** `GO2_STUB_OBJECTS` is a JSON object: each key is a COCO class name and each value is `{"x": number, "y": number}` in metres in the world frame, with an optional `"height_m": number > 0`; no other keys. Without `height_m` the class's entry in `DEFAULT_HEIGHTS_M` is used (bottle 0.25, cup 0.10, chair 0.90, person 1.70); a class without one is an error naming the class. `stub.parse_objects(data)` holds these rules and raises `ValueError`. One object per class.
- `sample_state()` reports `body_height` per posture (`BODY_HEIGHT_*_M`), `position` = [x, y, `body_height`], `imu_rpy` = [0, 0, yaw], and `velocity` (world frame) and `yaw_speed` from the stored command, so zero at rest. `mode`, `gait_type`, `foot_force`, `error_code` and `range_obstacle_raw` are `null`. `obstacle_m` is ray-cast from the body centre in the body's front, back, left and right directions against the objects in `GO2_STUB_OBJECTS`, each a disc of radius `OBSTACLE_RADIUS_M` (0.15 m, *tunable*); the distance is to the disc's edge, and a direction with no hit within `OBSTACLE_MAX_M` (5 m, *tunable*) is `null`. Stub robots can walk through objects: with the body centre inside a disc, every direction reports 0.0. The real mapping never yields 0.0, because it treats 0 as unknown. The stub models no walls or room bounds, so an empty world gives all `null`. The stub invents no raw array, because the encoding the firmware uses is unknown ([robot.md](robot.md#state-sampling)).
- All stub waits go through `backend.sleep()` and are multiplied by `stub.time_scale`. Skills never call `time.sleep` themselves.
- The stub is reset to `stub.initial_posture` at the origin at startup and by `go2 --reset-stub`, not between tasks ([running.md](../../docs/running.md)). The dispatcher does this with `stub.reset(posture, path)`, its only access to stub state; start-up posture on either backend comes from the `read_state` utility.

### Fault injection

Faults exercise every failure path with real processes. They apply to the stub backend only (`stub.faults` must be empty with `robot.backend = "real"`; that is a config error).

Configure them in config:

```toml
[stub]
faults = [ { step = 2, kind = "hang" } ]
```

or on the command line with `--fault STEP:KIND` (repeatable), which replaces the whole list ([running.md](../../docs/running.md)).

- `step` counts **dispatched** steps in the task, 1-based, across plans. Rejected steps are not counted. Steps must be unique.
- The dispatcher sets `GO2_STUB_FAULT` for that step only. Inside the process, the fault hits the first action call the skill makes (`Move`, `StopMove`, `StandDown`, `Stretch` or `detect`); later calls behave normally. For `detect_object` the fault is applied in the skill process (`stub.inject_detect_fault()`), after it connects to the vision service and before it sends the request, so the service never sees a fault.
- Utilities (`stop_move`, `read_state`) ignore faults (they call `stub.disable_faults()` first), so the stop path always works.
- The fault kinds are `stub.FaultKind` (`FAULT_KINDS`); the dispatcher's `stub.faults[].kind` config type is that same `Literal`, as `stub.initial_posture` is `stub.StubPosture`. The `stub.time_scale`, `stub.initial_posture` and `stub.state_file` config defaults are `stub.DEFAULT_TIME_SCALE`, `DEFAULT_POSTURE` and `DEFAULT_STATE_FILE`, the same values the stub uses when its env is unset.

| Kind | What the skill process does | Step outcome |
|---|---|---|
| `error` | the call returns 99 (`sdk_error`, e.g. `Move returned 99`); the detector raises a camera error (`camera_unavailable`) | `error` |
| `hang` | sleeps forever (not scaled) | `timeout` (killed, then `StopMove`) |
| `crash` | exits with code 139, no output | `malformed` |
| `garbage` | prints `not json` and exits 0 | `malformed` |

For one process run by hand, set `GO2_STUB_FAULT=<kind>` directly.

## Vision service

`detect_object` does not load a detector itself. It asks the vision service (`skills/vision_service.py`), a long-running process that loads the detector once, warms it up and answers requests on a Unix socket. On the real backend that is `RealDetector` (`skills/real_detector.py`: YOLO plus a `VideoClient` kept open across requests, [robot.md](robot.md#object-detection)); on the stub it is `StubDetector`, the simulated camera ([stub backend](#stub-backend)). The service only reads the camera: it never creates a `SportClient` and never commands motion. It grabs a frame only when a request arrives.

**Protocol** (`skills/vision_ipc.py`). `multiprocessing.connection` over the Unix socket at `GO2_VISION_SOCKET`, one request and one reply per connection, each a UTF-8 JSON object sent with `send_bytes` (never pickle), at most `MAX_MESSAGE_BYTES`:

- Request: `{"target": "<COCO class>"}`. The skill checks the target first; the service does not.
- Reply: the `DetectResult` fields `{"found": bool, "position", "closeness", "confidence"}` (the last three `null` when not found), or `{"error": {"code": "<error code>", "message": "<ExceptionType>: <message>"}}` with `camera_unavailable`, `bad_frame`, `weights_missing`, `backend_not_configured` or `detector_error`.

In `detect_object`, `connect_ms` is the connect and `exec_ms` the request until the reply. The service answers one connection at a time and drops a connection that sends no request within `REQUEST_WAIT_S` (1.0 s). If warm-up fails (weights missing, camera client not created), the service still becomes ready and logs the error to stderr; each request retries the part that failed and replies with its error code, so the model sees `weights_missing` rather than a dead service.

**Lifecycle contract** (owned by the dispatcher; the constants are in `skills/vision_service.py`):

- **Start:** `python -m skills.vision_service`, no arguments, working directory the base dir, environment `child_env(...)` with `fault` unset: the same call the executor makes for a skill, so `GO2_VISION_SOCKET` is `vision_socket_path(os.getpid())` = `<tempfile.gettempdir()>/go2-vision-<dispatcher pid>.sock` on both sides. Start it in its own session (`start_new_session=True`), with stdin `/dev/null`, stdout a pipe, and stderr (its log) to a file or inherited, never an unread pipe.
- **Ready:** the service writes exactly one line, `ready` (`READY_LINE`), to stdout once it is warmed up and listening; nothing else is ever written to stdout. Wait for it up to `STARTUP_TIMEOUT_S` (30.0 s, tunable). Exit before the line is a failed start; exit code 2 means `GO2_BACKEND` or `GO2_VISION_SOCKET` is missing or unknown.
- **Restart:** before each `detect_object` step, if the process has exited, start it again and wait for `ready`. A step whose service is down or slow is not retried: `detect_object` reports `vision_unavailable`.
- **Stop:** `SIGTERM`; it closes the listener, removes the socket file and exits 0. Send `SIGKILL` if it is still alive after `STOP_GRACE_S` (2.0 s).
- **Socket file:** created with mode `0600`. A file left by a killed service is removed by the next service at start.
- **Parent death:** the service runs the skills' orphan watchdog (`process.start_orphan_watchdog`, `GO2_PARENT_PID`): when its parent is gone it exits with code 137 within about 2.2 s, leaving the socket file for the next start to remove. Without `GO2_PARENT_PID` (a manual run) it runs until stopped.

## Adding a new skill

Example: a `stand` skill (recommended before experiments, see [roadmap.md](../../docs/roadmap.md#open-questions)).

1. Create `skills/stand.py` by copying `skills/sit.py` (a single SDK action with a settle wait via `motion.single_action`). A real `stand` would call `StandUp()` then `BalanceStand()`. Each SDK method must exist on the stub (`StubSportClient`) and work through `real.get_sport_client()`.
2. Create `skills/catalog/stand/SKILL.md` with frontmatter (`name: stand`, `entrypoint: skills.stand`, a one-line `description`, `params` if any) and the [body sections](#skillmd-body), written for the planner. If the body quotes a code constant, add it to the prose/constant sync test.
3. If the skill moves the robot, pass `cost=` (a `MotionCost`, or a function of params returning one); a timeout that depends on params is a function too (see `skills/walk.py`). If some observations should reach the model, list them in `context_observations`.
4. Write `body(params)` and end the module with `runner.run_skill(POLICY, body)`, which parses params, samples state before and after, and emits the response. The body returns a frozen `runner.SkillOutcome`, built only with `SkillOutcome.ok(observations=..., timing=...)` or `SkillOutcome.error(code, message, observations=..., timing=...)` so a status without its matching error fields cannot be built; `timing` holds `client_init_ms` and `exec_ms`. Get the sport client with `motion.sport_client()`, which inits DDS first so `client_init_ms` excludes it. Read params directly (`params["key"]`); do not re-check types, enums or ranges, which the dispatcher validates against the `SKILL.md`. Use `backend.sleep()`, never `time.sleep`. In motion loops, check `process.orphaned()` and always end with `StopMove()`.
5. Run `uv run go2 catalog`, run the skill by hand (below), and add tests (contract test in `tests/integration/test_skills.py`, policy test in `tests/integration/test_skill_policies.py`). The catalog golden file and the registry hash change: rewrite the golden file with `uv run pytest --update-golden` and review the diff ([testing.md](../../tests/docs/testing.md)).
6. Update this page (and [robot.md](robot.md) if the skill adds robot-side facts).

## Running a skill by hand

On the stub:

```bash
export GO2_BACKEND=stub GO2_STUB_STATE_FILE=logs/.stub_state.json GO2_STUB_TIME_SCALE=0.1
uv run python -m skills.walk '{"direction":"forward","distance_m":1}'
uv run python -m skills.turn '{"direction":"left","angle_deg":90}'
export GO2_VISION_SOCKET=logs/.vision.sock GO2_STUB_OBJECTS='{"chair":{"x":1.5,"y":0}}'
uv run python -m skills.vision_service &       # prints "ready"; stop it with: kill %1
uv run python -m skills.detect_object '{"target":"chair"}'
uv run python -m skills.detect_object '{"target":"bottle"}'
GO2_STUB_FAULT=crash uv run python -m skills.sit '{}'; echo "exit=$?"
uv run python -m skills.read_state '{}'
uv run python -m skills.stop_move '{}'
```

The vision service reads `GO2_STUB_OBJECTS` once at its start; restart it after changing the objects. Without `GO2_STUB_STATE_FILE` and `GO2_STUB_TIME_SCALE` the stub uses `logs/.stub_state.json` (relative to the working directory) and 0.1. On the real robot set `GO2_BACKEND=real GO2_IFACE=<nic>` (and `GO2_YOLO_WEIGHTS` for detection), with the robot supervised ([safety.md](../../docs/safety.md#supervised-operation-rules)). Pass all params: skills do not fill defaults.

## Design decisions

- **One subprocess per skill call, never reused.** The Unitree SDK initialises DDS through a process-wide singleton, so a fresh process guarantees a clean channel. It also lets a hung call be killed and isolates crashes in the native bindings.
- **Detection runs in a long-running [vision service](#vision-service); `detect_object` stays a short process that asks it.** Loading torch and YOLO, the first predict, DDS init and `VideoClient.Init` took about 5 s of every detection on the lab machine, while a warm predict takes 0.04 s. The service pays that once per dispatcher run. The rule above still holds for skills: `detect_object` is one process per call with the same response, timeout and kill path, and the service never touches the sport client, so the clean-channel and kill reasons, which are about motion, do not apply to it. Requests carry only a target and the service grabs a frame per request, so no state leaks between calls. IPC is `multiprocessing.connection` with JSON bytes, standard library only. Rejected: exporting YOLO to ONNX to drop torch from the skill process (still a model load and first inference per call, and a second model format to maintain); streaming frames in the background (camera traffic and a stale-frame question for no gain, since a frame costs one RPC per request); retrying `GetImageSample` on error 3104.
- **`walk` was split into `walk` and `turn`.** The predecessor's single range mixed metres and degrees. Separate skills give the model clearer parameters and allow separate travel and rotation budgets. `turn` uses **degrees** at the interface because that is what operators say; the skill converts to radians.
- **One common JSON response schema for all skills**, replacing the predecessor's three incompatible output formats, so the executor, the log and the context renderer handle every skill the same way.
- **Each skill owns its policy** (timeout formula, motion cost, observations shown to the model) as module constants next to the code that shares them, so a number like walking speed is defined once and tuned in one place.
- **`walk` and `turn` share one timeout rule, `motion.motion_timeout`, and one command period, `motion.CMD_PERIOD_S`,** because both run the same move loop: the fixed overhead and the margin on motion time are properties of that loop, not of a skill, so they are tuned in one place. Speeds (`VELOCITY_MPS`, `YAW_RATE_RPS`) and direction maps stay per skill, since the `SKILL.md` sync test reads them there. Rejected: per-skill copies of `BASE_S` and `FACTOR`, which could drift apart silently.
- **The SDK reply timeout `SPORT_CLIENT_TIMEOUT_S` (5.0 s) must fit inside every sport-client process timeout, so a lost reply gives `sdk_error`, not a kill.** A kill leaves no response, no `state_after` and no SDK code for the log, while `sdk_error` keeps all three. On the robot, a 90° `turn` whose `StopMove` reply was lost waited the old 10 s and was killed at its 12.4 s timeout. The move-loop base is derived from the SDK timeout, so the two cannot drift; the constant timeouts of `sit`, `stretch` and `stop_move` are checked by a test. Rejected: 2 s (too tight per operator decision); keeping 10 s, which no move-loop or single-action timeout covered.
- **A policy is one frozen `SkillPolicy` dataclass instance per skill, not a subclass per skill.** The skills differ only in data (a name, a constant or a small formula for timeout and cost, an observation list), so a value with optional callables says it in a few lines and the constants sit at module level where `run_skill(POLICY, body)` and the body use them without a class prefix. The dataclass lives in `skills/policy.py`, which imports only the standard library, so the dispatcher's own modules import the type without the runner. Rejected: a `SkillPolicy` base class with one `<Name>Policy` subclass per skill overriding `timeout_s()`/`motion_cost()`; it repeated boilerplate in every module and spread the constants across class attributes, without adding behaviour. The dispatcher still calls `timeout_s(params)` and `motion_cost(params)`, so it does not need to know whether a value is constant.
- **Utilities carry a `POLICY` like skills, as a plain `SkillPolicy` with the default cost and observations.** One mechanism for every module the dispatcher spawns, so the dispatcher reads a utility's name and timeout from its module instead of hard-coding them. The defaults fit both utilities (zero cost: neither commands motion), so a smaller utility-only type would add a second type without a field to drop that matters. Rejected: a `UTILITIES` map in `skills` (a second registry of names and timeouts beside the modules that own them); keeping utility timeouts as dispatcher config keys (`robot.*_timeout_s`), which gave timeouts two sources of truth, config for utilities and `POLICY` for skills.
- **Param and response validation happen on the dispatcher side; the response schema is defined once in `skills`.** Plan params are validated only by the dispatcher's plan and bounds validation against `SKILL.md`; skills read params directly. Skill responses are fully validated only by the dispatcher, but against the dataclasses in `skills/schema.py`, so the skill side constructs the very classes the dispatcher checks and there is no second definition to drift. The one rule that runs in both processes is the error-iff-status check in `SkillResponse.__post_init__`: it is part of the class, costs nothing, and pydantic reports its `ValueError` as a validation error. Rejected: separate skill-side validation of params or response types; the skill-side param checks only mattered for hand-run skills, where a missing key now reports `exception` instead of `invalid_params`.
- **The response schema is standard-library frozen dataclasses (`skills/schema.py`), validated by pydantic only in the dispatcher.** Skill processes must start fast and never import pydantic ([above](#no-side-effects-on-import)), yet the shape must be defined once. pydantic validates dataclasses directly; `__pydantic_config__ = {"extra": "forbid"}` is a plain dict, so `schema.py` needs no pydantic import. `RobotState` forbids extra keys like the other two: `asdict` and pydantic's dump would silently drop them, so a new robot field must be added to the class. A direct constructor call does no type checks, only `__post_init__`; type checks happen when the dispatcher parses the line. Rejected: pydantic models in `skills` (about 80 ms added to every spawn, `stop_move` included); dispatcher-only models plus a test that keeps the skill-side dicts in step (still two definitions); a JSON Schema file (needs code generation or a new dependency); `TypedDict` (pydantic needs `typing_extensions.TypedDict` on Python < 3.12); msgspec (a C dependency in the skill processes).
- **The `SKILL.md` schema and parser belong to `skills` (`skills/manifest.py`)**, so the package that defines the skills owns its catalog contract, and its rules are documented and tested here. pydantic is acceptable there because only the dispatcher process imports the parser; skill subprocesses never do, which an import-linter contract enforces for imports through the repo's own modules ([testing.md](../../tests/docs/testing.md#import-boundaries)). The registry hash stays in the dispatcher, because it also covers the system text and tool schema. Rejected: keeping the schema in the dispatcher's registry, which put the `SKILL.md` contract in the package that only consumes it.
- **The catalog is generated from the registry, never hand-written**, so skill sets of different granularity are presented to the model in the same form and stay comparable.
- **The `SKILL.md` body is model-facing, thin, and in a fixed section template that the parser enforces** (user decision). The planner lacked contract facts it needs to plan (posture preconditions, speeds), and those facts belong next to the skill they describe. The body holds only what the planner needs; human documentation moved to this page and [robot.md](robot.md). The same four headings for every skill and every skill set (granularity tier) keep tiers comparable: what differs between tiers is the skills, not how well or how much each one is described. All sections are required, with `None.` when nothing applies, so every skill has the same shape and a forgotten section is an error rather than a silent gap. The length cap stops bodies growing back into documentation. Mirrored numbers stay literal, guarded by a test, so the model reads plain numbers and the code stays the single source of truth for them. Rejected: frontmatter-only fields (`requires:`, `limits:` keys), because a skill is then just a typed function signature, not really a skill; free-form prose, because its length and quality would vary per skill and tier and confound the granularity comparison; Agent Skills style on-demand loading (the model reads a body only when it asks), because it adds a round-trip per skill and makes the context differ between calls.
- **An unsupported `detect_object` target is a skill error with suggestions; the 80-class COCO list stays out of the catalog.** Listing every class would add tokens to every call; the skill validates the target and suggests close matches, so the model can correct itself on the next call.
- **`detect_object`'s Limits say the camera cannot see an object under, very close to or behind it, so a target lost right after walking toward it was most likely reached or passed.** The model gets no pose or odometry and older sightings drop out of its context, so without this text it read a bottle it had walked past as lost and searched at random until the time limit. The suggested approach step of about 0.3 m is *(tunable)*; the camera offset of about 0.3 m ahead of the body centre that makes an object vanish once passed is *unverified* on the robot ([robot.md](robot.md#object-detection)).
- **State is sampled at the start and end of every step, for logging only.** v1 gives no verdicts. The samples provide data to set v2 verification thresholds before seeing any verification results, and supply the posture shown to the model ([roadmap.md](../../docs/roadmap.md#v2-plan)).
- **The stub replaces only the SDK layer inside the subprocess**, so process start, timeouts and kills are exercised for real offline.
- **The stub is pose-aware: it integrates commanded velocity and simulates the camera over objects placed in a world, reusing the real detector's classifier.** Detections then change as the robot moves (a chair behind the robot appears after a turn), the state samples carry a pose in the real `position` and `imu_rpy` fields, and position and closeness come from the same thresholds as on the robot (`skills/vision.py`), so the two backends cannot drift apart. Only what the robot supplies is simulated: velocity integration stands in for the robot's motion and estimator, and a pinhole projection stands in for the camera image and YOLO. The projection ignores lens distortion, occlusion and the object's vertical position, and the pose resets only with the posture, never between tasks. Rejected: keeping the static `{class: "position:closeness"}` table (`stub.detections`), alone or beside the world, because it answered the same whatever the robot did, so a plan that turned or walked before looking was never tested against what it would actually see.
- **`skills` owns the process environment contract, the posture type, the error codes, the fault kinds and the stub defaults; the dispatcher imports them.** These are contracts between the executor and the skill processes, and the skill side is where they are read. Defining them once (`skills/env.py`, `schema.Posture`, `schema.ErrorCode`, `stub.FaultKind`, the `stub.DEFAULT_*` values) removes the copies in the executor and config that could drift silently: a renamed variable, a new fault kind or a changed stub default used to need matching edits in both packages. `ErrorCode` is `class ErrorCode(str, Enum)` with `__str__` returning the value, because Python 3.10 has no `StrEnum`; the members serialise and compare as their plain strings, so the response line and the dispatcher's validation are unchanged. `SkillError.code` stays `str`, so pydantic does not reject a code the dispatcher does not know. `Posture` lives in `schema.py` next to `RobotState`, which uses it, rather than in `backend.py`. Rejected: a dispatcher-owned env builder (it would keep the variable names in the package that only sets them); typing `SkillError.code` as `ErrorCode` (an unknown code would turn a skill error into `malformed`).
- **The response is built from four sources, `SkillOutcome` + `StateSampler` + `total_ms` + `dds_init_ms`, each written by the code that owns it.** The body owns the outcome, the sampler owns the state fields and their timing, the backend owns the DDS init time (DDS is set up once per process, wherever it first happens), and `run_main` owns the total time and the exception mapping (`SkillOutcome.from_exception`). `run_main` creates the sampler before calling the body, so samples taken before an exception survive without the body writing into a shared result. Accepted edge case: any exception in `stop_move` after `StopMove()` returns (the settle `backend.sleep` on a malformed `GO2_STUB_TIME_SCALE`, or the after-sample raising `BackendNotConfigured`) drops `sdk_ret`, `client_init_ms` and `stop_call_ms` from the response; only misconfiguration reaches it. Rejected: a mutable `ResponseDraft` (a typed version of the old `out` dict: three writers still mutate one object, and it duplicates `SkillResponse`); plain locals in `run_main` with `try/finally` (it either duplicates the exception mapping in each utility or keeps utilities mutating a result argument).
- **Setup cost is split into `dds_init_ms` and `client_init_ms`; `state_ms` measures sampling only and `stop_call_ms` the `StopMove()` call only.** On the robot about 1 s of every motion step is setup. One setup field, or a `state_ms` that includes DDS init (the first sample is where a skill process first touches DDS), cannot tell DDS from sport client cost, nor sampling from setup. Each field measures one part, so per-step setup can be attributed and kill-to-stop latency is the sum of its parts ([safety.md](../../docs/safety.md#measured-latency)). `dds_init_ms` includes the import of the SDK channel module, because that import loads CycloneDDS and no other field would count it. Rejected: keeping `stop_call_ms` as the whole stop from utility start, because it would again mix DDS, client and call time; the whole stop is `stop_move.duration_ms` in the run log.
- **The dispatcher touches stub state only through `stub.reset(posture, path)`, never the state file or `write_posture`.** The state file layout stays a stub internal, so a richer stub state (the v2 stub upgrade) changes `reset` alone, and posture is read the same way on both backends, through `read_state`. Rejected: a stub `read_posture` call in the dispatcher, which needs a backend branch and a second posture path.
- **The shared skill code is three modules with one reason to change each: `policy.py` (`SkillPolicy`, `MotionCost`, read by the dispatcher), `process.py` (the saved stdout fd and the orphan watchdog) and `runner.py` (`SkillOutcome`, `StateSampler`, response building, `emit`, `run_main`, `run_skill`).** The imports run one way, `runner` → `backend` → `stub` → `process`, so no module imports another inside a function to dodge a cycle; `backend` still imports `real` and `stub` lazily, to select the backend and keep third-party imports out of import time. `process` is not named `io`, so it never shadows the standard library module. Rejected: one `result.py` holding all of it, which mixed the dispatcher's policy reads with process plumbing and needed a lazy import cycle with `backend` and `stub`.
- **Skills collapse an error message to one line but do not limit its length; the dispatcher owns the limit.** One limit in one place, so a message is never cut twice to two different lengths, and the run log keeps the skill's full message in `response`. Rejected: a skill-side cap as well (it was 300 characters against the dispatcher's 200, so the skill cut was dead for every view except the logged response).
- **Fault injection by dispatched step number** (error, hang, crash, garbage) covers each step outcome the executor can produce, with real processes.
- **No `integer` param type.** No skill used it; a whole-number parameter is declared `number`. Full decision in [loop-and-context.md](../../dispatcher/docs/loop-and-context.md).
