# Skills

The skill contract: how a skill is declared, invoked and answers, how the catalog the model sees is generated, the stub backend and fault injection, and how to add a skill. Robot-side behaviour (SDK calls, real backend, posture rule, settle waits) is in [robot.md](robot.md).

A skill has two parts:

1. `skills/<name>/SKILL.md`: YAML frontmatter that the registry reads, plus prose for humans. The prose is never sent to the LLM.
2. `src/go2_skills/<name>.py`: a module with a `main()` that runs as a subprocess, and a module-level `POLICY` object that the dispatcher reads.

v1 skills: `walk`, `turn`, `sit`, `stretch`, `detect_object`. Two utilities, `stop_move` and `read_state`, run the same way but are not skills: they have no `SKILL.md` and the model never sees them. The registry loads every subfolder of `skills.dir` that has a `SKILL.md`, so a different skill set (for example another granularity tier) is a different folder ([running.md](running.md)).

## SKILL.md frontmatter

The YAML block between the first two lines that are exactly `---` (the file must start with `---`), parsed with `yaml.safe_load`.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string matching `^[a-z][a-z0-9_]*$` | yes | Must equal the folder name and `POLICY.name`. |
| `entrypoint` | string | yes | Module run with `python -m`, for example `go2_skills.walk`. |
| `description` | one-line string | yes | Shown in the catalog. |
| `params` | mapping name → ParamSpec | no | Parameters, in catalog order. Absent means no parameters. |
| `expect` | any | no | Reserved for v2 verification. Accepted and ignored. |

ParamSpec (parameter names match `^[a-z][a-z0-9_]*$`):

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `type` | `number` \| `integer` \| `string` \| `enum` | all | Required. |
| `description` | non-empty string | all | Required. Short. |
| `values` | non-empty list of unique lowercase strings | `enum` | Required for `enum`, not allowed otherwise. |
| `min`, `max` | finite number | `number`, `integer` | Optional, inclusive, `min <= max`. |
| `default` | value of the param's type | all | Optional. If absent, the parameter is required. Must pass the param's own checks. |
| `unit` | non-empty string | `number`, `integer` | Optional. Shown in the catalog. |

Any other key, a missing required key or an invalid value is a registry error, as are: an entrypoint that cannot be imported, a module without `POLICY`, a `POLICY` that is not a `SkillPolicy`, `POLICY.name` ≠ `name`, a missing skills folder, or zero skills. Transports print `Registry error: <message naming the file>` and exit 2.

Example (`skills/walk/SKILL.md`):

```yaml
---
name: walk
entrypoint: go2_skills.walk
description: Walk in a straight line forward, backward, or sideways by a distance, then stop.
params:
  direction:
    type: enum
    values: [forward, backward, left, right]
    description: Direction of travel relative to the robot's current heading.
  distance_m:
    type: number
    min: 0.1
    max: 3.0
    default: 0.9
    unit: metres
    description: Distance to travel.
---
```

The dispatcher checks every planned step against these declarations before anything runs ([loop-and-context.md](loop-and-context.md#step-bounds)).

## Catalog and registry hash

The catalog (`Registry.catalog_text()`) is generated from the frontmatter, skills sorted by name, one line per parameter, no trailing newline. It is sent as `system[1]` = `"## Skills\n" + catalog`. Current catalog (`tests/golden/catalog.txt`):

```
detect_object: Look through the front camera once and report whether an object is visible, where it is in the frame, and roughly how close it is. Does not move the robot.
  - target (required): text. Object to look for, as a lowercase COCO class name (for example chair, person, bottle, cell phone).
sit: Lower the robot's body to the ground (sit or lie down).
  - no parameters
stretch: Perform the robot's built-in stretch routine.
  - no parameters
turn: Turn in place to the left or right by an angle, then stop.
  - direction (required): one of left, right. Which way to turn.
  - angle_deg (optional, default 45): number from 5 to 180 degrees. Angle to turn.
walk: Walk in a straight line forward, backward, or sideways by a distance, then stop.
  - direction (required): one of forward, backward, left, right. Direction of travel relative to the robot's current heading.
  - distance_m (optional, default 0.9): number from 0.1 to 3 metres. Distance to travel.
```

Type phrases: `one of a, b`; `text`; `number from X to Y`, `number, at least X`, `number, at most Y` or `number` (likewise `integer`), followed by the unit.

The **registry hash** is the first 16 hex characters of SHA-256 over `system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True)`. It identifies the whole prompt surface, so it changes with the skill set, any `SKILL.md` wording, the system text and the horizon (which appears in the system text and the tool schema). It is logged in `task_start` and `index.jsonl`; runs with different hashes are not directly comparable. `go2-dispatch catalog` prints the system text, catalog, tool schema and hash.

## Invocation

```
python -m go2_skills.<name> '<params-json>'
```

- `argv[1]` is a JSON object with the **filled** params: checked, normalised and with defaults filled in by the dispatcher. Always present (`{}` for no params).
- Environment, set by the executor (secrets `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` removed):

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

- The process runs with the base dir as working directory, in its own session (`start_new_session=True`), so a kill reaches the whole process group ([safety.md](safety.md#the-stop-path)).
- **Output:** exactly one JSON line on the real stdout, written by `result.emit()`. Everything else goes to stderr: `capture_stdout()` points fd 1 at stderr first, so output from the SDK, CycloneDDS, ultralytics or C code cannot corrupt the response line. The executor reads the last non-empty stdout line and requires its `skill` to match.
- **Exit code:** 0 for `status = "ok"`, 1 for `status = "error"` (`emit` uses `os._exit`, because DDS threads can hang normal interpreter shutdown). No valid response line means step outcome `malformed`.
- Skills check only presence, type and enum membership (`invalid_params`) and apply no defaults, so a skill run by hand is not range-limited and needs all params. Only `detect_object.target` is trimmed and lower-cased by the skill.

## Response schema

Every skill and utility prints one `SkillResponse` (`schema_version` 1):

| Field | Meaning |
|---|---|
| `schema_version` | Always `1`. |
| `skill` | Skill name; must match the skill that was run. |
| `status` | `ok` or `error`. |
| `observations` | Skill-specific results. |
| `error` | `{code, message}` if and only if `status = "error"`, else `null`. One line, at most 300 characters. |
| `state_before`, `state_after` | Robot state sampled at start and end (utilities: `state_after` only). |
| `state_error` | Why a state sample failed (`before: ...` / `after: ...`, joined with `; `), else `null`. |
| `timing` | `init_ms`, `exec_ms`, `state_ms`, `total_ms`; `stop_move` adds `stop_call_ms`. |

Robot state fields: `t` (unix time), `backend`, `posture` (`standing` / `sitting` / `unknown`), `mode`, `gait_type`, `body_height`, `position` [x, y, z], `velocity`, `yaw_speed`, `imu_rpy`, `foot_force`, `error_code`. Missing or non-finite values are `null`. State is **logged only**; the model sees only the derived posture ([loop-and-context.md](loop-and-context.md#user-message)). Field sources and the posture rule: [robot.md](robot.md).

`ok` example (stub, `turn`):

```json
{"schema_version":1,"skill":"turn","status":"ok","observations":{"direction":"left","angle_deg":90.0,"duration_s":1.6,"sdk_ret":0},"error":null,"state_before":{"t":1790843763.67,"backend":"stub","posture":"standing","mode":null,"gait_type":null,"body_height":0.32,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_after":{"t":1790843763.69,"backend":"stub","posture":"standing","mode":null,"gait_type":null,"body_height":0.32,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_error":null,"timing":{"init_ms":0.007,"exec_ms":22.999,"state_ms":5.272,"total_ms":28.342}}
```

`error` example (stub, `walk` while sitting; exit code 1):

```json
{"schema_version":1,"skill":"walk","status":"error","observations":{"direction":"forward","distance_m":0.5,"duration_s":0.0,"sdk_ret":1},"error":{"code":"sdk_error","message":"Move returned 1"},"state_before":{"t":1790843774.54,"backend":"stub","posture":"sitting","mode":null,"gait_type":null,"body_height":0.08,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_after":{"t":1790843774.54,"backend":"stub","posture":"sitting","mode":null,"gait_type":null,"body_height":0.08,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_error":null,"timing":{"init_ms":0.009,"exec_ms":0.051,"state_ms":5.438,"total_ms":5.545}}
```

## The skills

| Skill | Params | Action | Observations | Shown to the model on `ok` |
|---|---|---|---|---|
| `walk` | `direction` ∈ forward/backward/left/right; `distance_m` 0.1–3.0, default 0.9 | `Move` at 0.3 m/s every 0.1 s for `distance_m / 0.3` s, then `StopMove` | `direction`, `distance_m`, `duration_s` (commanded), `sdk_ret`, `orphaned` (only if set) | nothing |
| `turn` | `direction` ∈ left/right; `angle_deg` 5–180, default 45 | `Move(0, 0, ±1.0 rad/s)` every 0.1 s for `radians(angle_deg)` s, then `StopMove` | `direction`, `angle_deg`, `duration_s`, `sdk_ret`, `orphaned` | nothing |
| `sit` | none | `StandDown()`, then a settle wait | `sdk_ret` | nothing |
| `stretch` | none | `Stretch()`, then a settle wait | `sdk_ret` | nothing |
| `detect_object` | `target`: a COCO class name | one camera frame + YOLO; never moves | `target`, `object_found`, `position` (left/center/right), `closeness` (near/medium/far), `confidence` | `object_found`, `position`, `closeness`, `confidence` |

Walk and turn are open-loop: they command a velocity for the time the motion should take; nothing measures the distance covered. Not finding an object is `status = ok` with `object_found: false`, not an error. Ranges are starting values (*tunable*).

### Error codes

| Code | Skills | Meaning |
|---|---|---|
| `invalid_params` | all | Params are not a JSON object, a required key is missing, or a value has the wrong type or enum value. |
| `sdk_error` | walk, turn, sit, stretch, stop_move | An SDK call returned non-zero. Message `<Call> returned <code>` (walk/turn add `; StopMove returned <code>` if the cleanup stop also failed). Motion skills send `StopMove` first. |
| `backend_not_configured` | all | `GO2_BACKEND` missing or unknown, or `GO2_IFACE` empty on the real backend. |
| `exception` | all | Unexpected exception: `<Type>: <first line>`. The traceback goes to stderr. |
| `unsupported_object` | detect_object | Target is not one of the 80 COCO classes, for example `'phone' is not a detectable object. Closest supported: cell phone, spoon, horse.` (up to 3 close matches, if any). |
| `camera_unavailable` | detect_object | The camera returned an error or no data (also the stub's `error` fault). |
| `bad_frame` | detect_object | The image could not be decoded. |
| `weights_missing` | detect_object | The YOLO weights file does not exist. |
| `state_unavailable` | read_state | No state could be sampled. |

Detector errors have the message `<ExceptionType>: <message>`. Outcomes the dispatcher adds on its own (`timeout`, `malformed`, `rejected`, `motion_budget_exceeded`, `interrupted`) are listed in [loop-and-context.md](loop-and-context.md#step-outcomes).

## Policies

Each skill module defines `class <Name>Policy(SkillPolicy)` and `POLICY = <Name>Policy()`. The dispatcher imports the module only to read `POLICY`:

```python
class SkillPolicy:
    name: str = ""
    context_observations: tuple[str, ...] = ()   # observation keys shown to the LLM on ok
    def timeout_s(self, params: dict) -> float: ...
    def motion_cost(self, params: dict) -> MotionCost:   # default: zero
        return MotionCost()                              # MotionCost(distance_m, rotation_deg)
```

`params` are the filled, checked params. Every number is a class attribute, tunable in one place:

| Policy | Timeout | Motion cost | Constants |
|---|---|---|---|
| `WalkPolicy` | `BASE_S + FACTOR × distance_m / VELOCITY_MPS` | `distance_m` | `BASE_S = 10.0`, `FACTOR = 1.5` (*tunable*); `VELOCITY_MPS = 0.3` |
| `TurnPolicy` | `BASE_S + FACTOR × radians(angle_deg) / YAW_RATE_RPS` | `rotation_deg = angle_deg` | `BASE_S = 10.0`, `FACTOR = 1.5` (*tunable*); `YAW_RATE_RPS = 1.0` |
| `SitPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 15.0`, `SETTLE_S = 3.0` (*tunable*) |
| `StretchPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 20.0`, `SETTLE_S = 6.0` (*tunable*) |
| `DetectObjectPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 45.0` (*tunable*; covers YOLO load on CPU) |

`BASE_S` covers process start, SDK init and the two state samples; `FACTOR` is a margin on the commanded motion time. The timeout covers the whole process from start to exit. Settle waits are explained in [robot.md](robot.md). The motion cost feeds the motion budget ([safety.md](safety.md#motion-budget)).

## No side effects on import

Importing a skill module must do nothing: no SDK import, no DDS init, no argument parsing, no output. All work happens in `main()`, under `if __name__ == "__main__": main()`. At top level a skill imports only the standard library and `go2_skills`; `go2_skills` never imports `go2_dispatcher`. Third-party imports (`unitree_sdk2py`, `cv2`, `ultralytics`, `numpy`) happen inside functions in `go2_skills/real.py`. A test imports every `go2_skills` module in a fresh interpreter and checks that none of these were loaded. This is what lets the dispatcher read policies without touching the SDK.

## Stub backend

The stub (`src/go2_skills/stub.py`) replaces only the SDK layer inside the skill process; processes, timeouts, kills and `StopMove` run for real. It keeps posture in a JSON state file (`{"posture": "standing" | "sitting"}`; a missing file means standing), written atomically.

| Call | While standing | While sitting |
|---|---|---|
| `Move` | 0 | 1 (not standing) |
| `StopMove` | 0 | 0 |
| `StandDown` | 0; posture → sitting; waits 1.5 s | 0; no change |
| `Stretch` | 0; waits 3.0 s | 1 |

- `detect(target)` waits 0.5 s and returns found (`confidence` 0.9, position and closeness from the entry) if the target is listed in `stub.detections`, else not found.
- `sample_state()` returns `body_height` 0.32 (standing) or 0.08 (sitting); other fields are `null`.
- All stub waits go through `backend.sleep()` and are multiplied by `stub.time_scale`. Skills never call `time.sleep` themselves.
- The stub is reset to `stub.initial_posture` at startup and by `go2-dispatch --reset-stub`, not between tasks ([running.md](running.md)).

### Fault injection

Faults exercise every failure path with real processes. They apply to the stub backend only (`stub.faults` must be empty with `robot.backend = "real"`; that is a config error).

Configure them in config:

```toml
[stub]
faults = [ { step = 2, kind = "hang" } ]
```

or on the command line, where `--fault STEP:KIND` (repeatable) replaces the whole list:

```bash
uv run go2-dispatch --fault 2:hang run "turn left, then walk forward one metre"
uv run go2-dispatch --fault 1:error --fault 3:crash batch tasks.txt
```

- `step` counts **dispatched** steps in the task, 1-based, across plans. Rejected steps are not counted. Steps must be unique.
- The dispatcher sets `GO2_STUB_FAULT` for that step only. Inside the process, the fault hits the first action call the skill makes (`Move`, `StopMove`, `StandDown`, `Stretch` or `detect`); later calls behave normally.
- Utilities (`stop_move`, `read_state`) ignore faults, so the stop path always works.

| Kind | What the skill process does | Step outcome |
|---|---|---|
| `error` | the call returns 99 (`sdk_error`, e.g. `Move returned 99`); the detector raises a camera error (`camera_unavailable`) | `error` |
| `hang` | sleeps forever (not scaled) | `timeout` (killed, then `StopMove`) |
| `crash` | exits with code 139, no output | `malformed` |
| `garbage` | prints `not json` and exits 0 | `malformed` |

For one process run by hand, set `GO2_STUB_FAULT=<kind>` directly. `GO2_STUB_NOISE=1` (tests only) makes the stub write junk to stdout, to check that the response line stays clean.

## Adding a new skill

Example: a `stand` skill (recommended before experiments, see [roadmap.md](roadmap.md#open-questions)).

1. Create `src/go2_skills/stand.py`:
   ```python
   from go2_skills import motion, result
   from go2_skills.policy_base import SkillPolicy

   SKILL = "stand"

   class StandPolicy(SkillPolicy):
       name = "stand"
       TIMEOUT_S = 15.0   # tunable
       SETTLE_S = 3.0     # tunable
       def timeout_s(self, p):
           return self.TIMEOUT_S

   POLICY = StandPolicy()

   def body(params: dict):
       # returns (status, observations, error_code, error_message, timing)
       return motion.single_action("StandUp", StandPolicy.SETTLE_S)

   def main() -> None:
       result.run_skill(SKILL, body)

   if __name__ == "__main__":
       main()
   ```
   A real `stand` would call `StandUp()` then `BalanceStand()`. Each SDK method must exist on the stub (`StubSportClient`) and work through `real.get_sport_client()`.
2. Create `skills/stand/SKILL.md` with frontmatter (`name: stand`, `entrypoint: go2_skills.stand`, a one-line `description`, `params` if any) and a short prose section.
3. If the skill moves the robot, return a `MotionCost` from `motion_cost()`. If some observations should reach the model, list them in `context_observations`.
4. Use `backend.sleep()`, never `time.sleep`. In motion loops, check `result.orphaned()` and always end with `StopMove()`.
5. Run `uv run go2-dispatch catalog`, run the skill by hand (below), and add tests (contract test in `tests/integration/test_skills.py`, policy test in `tests/unit/test_skill_policies.py`). The catalog golden file and the registry hash change: rewrite the golden file with `uv run pytest --update-golden` and review the diff ([testing.md](testing.md)).
6. Update this page (and [robot.md](robot.md) if the skill adds robot-side facts).

## Running a skill by hand

On the stub:

```bash
export GO2_BACKEND=stub GO2_STUB_STATE_FILE=runs/.stub_state.json GO2_STUB_TIME_SCALE=0.1
uv run python -m go2_skills.walk '{"direction":"forward","distance_m":1}'
uv run python -m go2_skills.detect_object '{"target":"chair"}'
GO2_STUB_DETECTIONS='{"chair":"center:near"}' uv run python -m go2_skills.detect_object '{"target":"chair"}'
GO2_STUB_FAULT=crash uv run python -m go2_skills.sit '{}'; echo "exit=$?"
uv run python -m go2_skills.read_state '{}'
uv run python -m go2_skills.stop_move '{}'
```

Without `GO2_STUB_STATE_FILE` and `GO2_STUB_TIME_SCALE` the stub uses `runs/.stub_state.json` (relative to the working directory) and 0.1. On the real robot set `GO2_BACKEND=real GO2_IFACE=<nic>` (and `GO2_YOLO_WEIGHTS` for detection), with the robot supervised ([safety.md](safety.md#supervised-operation-rules)). Pass all params: skills do not fill defaults.

## Design decisions

- **One subprocess per skill call, never reused.** The Unitree SDK initialises DDS through a process-wide singleton, so a fresh process guarantees a clean channel. It also lets a hung call be killed and isolates crashes in the native bindings.
- **`walk` was split into `walk` and `turn`.** The predecessor's single range mixed metres and degrees. Separate skills give the model clearer parameters and allow separate travel and rotation budgets. `turn` uses **degrees** at the interface because that is what operators say; the skill converts to radians.
- **One common JSON response schema for all skills**, replacing the predecessor's three incompatible output formats, so the executor, the log and the context renderer handle every skill the same way.
- **Each skill owns its policy** (timeout formula, motion cost, observations shown to the model) as class constants next to the code that shares them, so a number like walking speed is defined once and tuned in one place.
- **The catalog is generated from the registry, never hand-written**, so skill sets of different granularity are presented to the model in the same form and stay comparable. The `SKILL.md` prose never reaches the model.
- **An unsupported `detect_object` target is a skill error with suggestions; the 80-class COCO list stays out of the catalog.** Listing every class would add tokens to every call; the skill validates the target and suggests close matches, so the model can correct itself on the next call.
- **State is sampled at the start and end of every step, for logging only.** v1 gives no verdicts. The samples provide data to set v2 verification thresholds before seeing any verification results, and supply the posture shown to the model ([roadmap.md](roadmap.md#v2-plan)).
- **The stub replaces only the SDK layer inside the subprocess**, so process start, timeouts and kills are exercised for real offline. It remembers sitting or standing only: enough to run every failure path. Simulating motion is a v2 prerequisite ([roadmap.md](roadmap.md#stub-upgrade-prerequisite)).
- **Fault injection by dispatched step number** (error, hang, crash, garbage) covers each step outcome the executor can produce, with real processes.
