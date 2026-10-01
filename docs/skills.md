# Skills

A skill has two parts. Spec: §7–§10.

1. `skills/<name>/SKILL.md`: YAML frontmatter that the registry reads, plus prose for humans. The prose is never sent to the LLM.
2. `src/go2_skills/<name>.py`: a module with a `main()` that runs in a subprocess, and a module-level `POLICY` object that the dispatcher reads.

v1 skills: `walk`, `turn`, `sit`, `stretch`, `detect_object`. There are also two utilities that are not skills: `stop_move` and `read_state`. They are not in `skills/` and the LLM never sees them.

## SKILL.md frontmatter

The frontmatter is the YAML block between the first two lines that are exactly `---`. The file must start with `---`. It is parsed with `yaml.safe_load`.

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string matching `^[a-z][a-z0-9_]*$` | yes | Must equal the folder name and `POLICY.name`. |
| `entrypoint` | string | yes | Module run with `python -m`, for example `go2_skills.walk`. |
| `description` | one-line string | yes | Shown in the catalog. |
| `params` | mapping name → ParamSpec | no | Parameters, in catalog order. If absent, the skill has no parameters. |
| `expect` | any | no | Reserved for v2. Accepted and ignored. |

ParamSpec (parameter names must match `^[a-z][a-z0-9_]*$`):

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `type` | `number` \| `integer` \| `string` \| `enum` | all | Required. |
| `description` | string | all | Required. Short. |
| `values` | non-empty list of unique lowercase strings | `enum` | Required for `enum`, not allowed for other types. |
| `min`, `max` | finite number | `number`, `integer` | Optional, inclusive. `min <= max`. |
| `default` | value of the param's type | all | Optional. If absent, the parameter is required. The default must pass the parameter's own checks. |
| `unit` | string | `number`, `integer` | Optional. Shown in the catalog. |

Any other key, a missing required key, or an invalid value is a registry error. The transports print `Registry error: <message naming the file>` and exit 2. Other registry errors: the entrypoint cannot be imported, the module has no `POLICY`, `POLICY` is not a `SkillPolicy`, `POLICY.name` does not equal `name`, the folder is missing, or there are zero skills.

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

The catalog line for this skill is generated from the frontmatter (run `go2-dispatch catalog` to see the full catalog):

```
walk: Walk in a straight line forward, backward, or sideways by a distance, then stop.
  - direction (required): one of forward, backward, left, right. Direction of travel relative to the robot's current heading.
  - distance_m (optional, default 0.9): number from 0.1 to 3 metres. Distance to travel.
```

## Invocation

```
python -m go2_skills.<name> '<params-json>'
```

- `argv[1]` is a JSON object with the **filled** params: the dispatcher has already checked them, normalised them and filled in defaults. It is always present (`{}` if there are no params).
- The executor sets these environment variables (secrets removed):

| Variable | Meaning |
|---|---|
| `GO2_BACKEND` | `real` or `stub`. If missing or unknown, the skill returns `backend_not_configured`. |
| `GO2_IFACE` | Network interface (real backend). |
| `GO2_YOLO_WEIGHTS` | Absolute path to the YOLO weights. |
| `GO2_STUB_STATE_FILE` | Stub state file (absolute path). |
| `GO2_STUB_TIME_SCALE` | Stub time scale. |
| `GO2_STUB_DETECTIONS` | JSON `{class: "position:closeness"}`. |
| `GO2_STUB_FAULT` | Fault kind. Set only for the faulted step. |
| `GO2_PARENT_PID` | Dispatcher PID, for the orphan watchdog. |
| `PYTHONUNBUFFERED` | `1`. |

- The process runs with `cwd` = base dir, in its own session (`start_new_session=True`), so a kill reaches the whole process group.
- **Output:** exactly one JSON line on the real stdout, written by `result.emit()`. Everything else goes to stderr. At start, `capture_stdout()` points fd 1 at stderr, so output from the SDK, CycloneDDS, ultralytics or C code cannot corrupt the response line.
- **Exit code:** 0 for `status = "ok"`, 1 for `status = "error"`. Any other exit means there is no valid response, and the step outcome is `malformed`.
- Skills check only presence, type and enum membership (`invalid_params`), so they are safe to run by hand. The dispatcher enforces ranges.

## Response schema

Every skill and utility prints one `SkillResponse` (schema version 1):

| Field | Meaning |
|---|---|
| `schema_version` | Always `1`. |
| `skill` | Skill name. Must match the skill that was run. |
| `status` | `ok` or `error`. |
| `observations` | Skill-specific results. |
| `error` | `{code, message}` when `status = "error"`, else `null`. The message is one line, at most 300 chars. |
| `state_before`, `state_after` | Robot state sampled at start and end (utilities: `state_after` only). |
| `state_error` | Why a state sample failed, if one did (messages joined with `; `). |
| `timing` | `init_ms`, `exec_ms`, `state_ms`, `total_ms` (`stop_move` adds `stop_call_ms`). |

Robot state fields: `t` (unix time), `backend`, `posture` (`standing` / `sitting` / `unknown`), `mode`, `gait_type`, `body_height`, `position` [x, y, z], `velocity`, `yaw_speed`, `imu_rpy`, `foot_force`, `error_code`. Missing or non-finite values are `null`. State is **logged only**. The LLM sees only the derived posture.

`ok` example (stub, `turn`):

```json
{"schema_version":1,"skill":"turn","status":"ok","observations":{"direction":"left","angle_deg":90.0,"duration_s":1.6,"sdk_ret":0},"error":null,"state_before":{"t":1790843763.67,"backend":"stub","posture":"standing","mode":null,"gait_type":null,"body_height":0.32,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_after":{"t":1790843763.69,"backend":"stub","posture":"standing","mode":null,"gait_type":null,"body_height":0.32,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_error":null,"timing":{"init_ms":0.007,"exec_ms":22.999,"state_ms":5.272,"total_ms":28.342}}
```

`error` example (stub, `walk` while sitting; exit code 1):

```json
{"schema_version":1,"skill":"walk","status":"error","observations":{"direction":"forward","distance_m":0.5,"duration_s":0.0,"sdk_ret":1},"error":{"code":"sdk_error","message":"Move returned 1"},"state_before":{"t":1790843774.54,"backend":"stub","posture":"sitting","mode":null,"gait_type":null,"body_height":0.08,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_after":{"t":1790843774.54,"backend":"stub","posture":"sitting","mode":null,"gait_type":null,"body_height":0.08,"position":null,"velocity":null,"yaw_speed":null,"imu_rpy":null,"foot_force":null,"error_code":null},"state_error":null,"timing":{"init_ms":0.009,"exec_ms":0.051,"state_ms":5.438,"total_ms":5.545}}
```

## The skills

| Skill | Params | Action | Observations | Context shows (on `ok`) |
|---|---|---|---|---|
| `walk` | `direction` ∈ forward/backward/left/right; `distance_m` 0.1–3.0, default 0.9 | `Move` at 0.3 m/s, 10 Hz, for `distance_m / 0.3` s, then `StopMove` | `direction`, `distance_m`, `duration_s` (commanded), `sdk_ret`, `orphaned` (only if set) | nothing |
| `turn` | `direction` ∈ left/right; `angle_deg` 5–180, default 45 | `Move(0, 0, ±1.0 rad/s)` at 10 Hz, then `StopMove` | `direction`, `angle_deg`, `duration_s`, `sdk_ret`, `orphaned` | nothing |
| `sit` | none | `StandDown()`, then a settle wait | `sdk_ret` | nothing |
| `stretch` | none | `Stretch()`, then a settle wait | `sdk_ret` | nothing |
| `detect_object` | `target`: a COCO class name | one camera frame + YOLO (conf 0.4); never moves | `target`, `object_found`, `position` (left/center/right), `closeness` (near/medium/far), `confidence` | `object_found`, `position`, `closeness`, `confidence` |

Motion is open-loop: walk and turn command a velocity for the time the motion should take. Nothing measures the distance actually covered. Not finding an object is `status = ok` with `object_found: false`, not an error.

### Error codes

| Code | Skills | Meaning |
|---|---|---|
| `invalid_params` | all | Params are not a JSON object, a required key is missing, or a value has the wrong type or enum value. |
| `sdk_error` | walk, turn, sit, stretch, stop_move | An SDK call returned non-zero. Message `"<Call> returned <code>"`. Motion skills send `StopMove` first. |
| `backend_not_configured` | all | `GO2_BACKEND` missing or unknown, or `GO2_IFACE` empty on the real backend. |
| `exception` | all | Unexpected exception: `"<Type>: <first line>"`. The traceback goes to stderr. |
| `unsupported_object` | detect_object | Target not in `COCO_CLASSES`, for example `'phone' is not a detectable object. Closest supported: cell phone, spoon, horse.` (OD-8). |
| `camera_unavailable` | detect_object | Camera returned an error or empty data (also the stub's `error` fault). |
| `bad_frame` | detect_object | The image could not be decoded. |
| `weights_missing` | detect_object | The YOLO weights file does not exist. |
| `state_unavailable` | read_state | No state message could be sampled. |

The dispatcher adds outcomes of its own that are not skill codes: `timeout`, `malformed`, `rejected` (`bounds`), `motion_budget_exceeded`, `interrupted` (`stopped_by_operator`, `task_time_limit`, `shutdown`). See `docs/loop-and-context.md`.

## Policies

Each skill module defines `class <Name>Policy(SkillPolicy)` and `POLICY = <Name>Policy()`. The dispatcher imports the module only to read `POLICY`:

```python
class SkillPolicy:
    name: str = ""
    context_observations: tuple[str, ...] = ()   # observation keys shown to the LLM on ok
    def timeout_s(self, params: dict) -> float: ...
    def motion_cost(self, params: dict) -> MotionCost:   # default: zero
        return MotionCost()
```

`params` are the filled, checked params. Every number is a **class attribute**, so it can be tuned in one place and monkeypatched in tests:

| Policy | Timeout | Motion cost | Constants |
|---|---|---|---|
| `WalkPolicy` | `BASE_S + FACTOR × distance_m / VELOCITY_MPS` | `distance_m` | `BASE_S = 10.0`, `FACTOR = 1.5` (sketch); `VELOCITY_MPS = 0.3` |
| `TurnPolicy` | `BASE_S + FACTOR × radians(angle_deg) / YAW_RATE_RPS` | `rotation_deg = angle_deg` | `BASE_S = 10.0`, `FACTOR = 1.5` (sketch); `YAW_RATE_RPS = 1.0` |
| `SitPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 15.0`, `SETTLE_S = 3.0` (sketch; OD-10) |
| `StretchPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 20.0`, `SETTLE_S = 6.0` (sketch; OD-10) |
| `DetectObjectPolicy` | `TIMEOUT_S` | zero | `TIMEOUT_S = 45.0` (sketch; YOLO load on CPU) |

`BASE_S` covers process start, SDK init and the two state samples. `FACTOR` is a safety margin on the commanded motion time. The timeout covers the whole process, from start to exit.

## No side effects on import

Importing a skill module must do nothing: no SDK import, no DDS init, no argument parsing, no output. All work happens in `main()`, under `if __name__ == "__main__": main()`. At module top level, a skill may import only the standard library and `go2_skills` modules. `go2_skills` never imports `go2_dispatcher`. Third-party imports (`unitree_sdk2py`, `cv2`, `ultralytics`, `numpy`) are made inside functions in `go2_skills/real.py`. A test imports every `go2_skills` module in a fresh interpreter and checks that none of these were loaded.

## Orphan watchdog

`result.start_orphan_watchdog()` starts a daemon thread that checks `os.getppid()` every 0.2 s against `GO2_PARENT_PID`. If the parent has changed (the dispatcher died), it sets the `ORPHANED` flag. Motion loops check `result.orphaned()` on every iteration, break, and send `StopMove`. If the process is still alive 2.0 s later, it exits with `os._exit(137)`. The watchdog does nothing when `GO2_PARENT_PID` is unset (manual runs). `stop_move` and `read_state` do not use it, so a StopMove still finishes when the parent has died.

## Stub rules

The stub (`src/go2_skills/stub.py`) replaces only the SDK layer inside the skill process. It keeps posture in a JSON state file (`{"posture": "standing" | "sitting"}`; a missing file means standing). The file is written atomically.

| Call | While standing | While sitting |
|---|---|---|
| `Move` | 0 | 1 (`STUB_ERR_NOT_STANDING`) |
| `StopMove` | 0 | 0 |
| `StandDown` | 0; posture → sitting; waits 1.5 s | 0; no change |
| `Stretch` | 0; waits 3.0 s | 1 |

- `detect(target)` waits 0.5 s. It returns found with `confidence = 0.9` if the target is listed in `stub.detections`, else not found.
- `sample_state()` returns `body_height` 0.32 (standing) or 0.08 (sitting); the other fields are `null`.
- All stub waits go through `backend.sleep()` and are multiplied by `stub.time_scale`. Skills never call `time.sleep` themselves.
- Fault kinds (`error`, `hang`, `crash`, `garbage`) are listed in `docs/running.md`. A fault applies to the first action call. Utilities ignore faults.

## Adding a new skill

Example: a `stand` skill (OD-1).

1. Create `src/go2_skills/stand.py`:
   ```python
   from go2_skills import motion, result
   from go2_skills.policy_base import SkillPolicy

   SKILL = "stand"

   class StandPolicy(SkillPolicy):
       name = "stand"
       TIMEOUT_S = 15.0   # (sketch)
       SETTLE_S = 3.0     # (sketch)
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
   A real `stand` would call `StandUp()` then `BalanceStand()`. The SDK method must also exist on the stub (`StubSportClient` in `stub.py`) and work through `real.get_sport_client()`.
2. Create `skills/stand/SKILL.md` with frontmatter (`name: stand`, `entrypoint: go2_skills.stand`, a one-sentence `description`, `params` if any) and a short prose section.
3. If the skill moves the robot, return a `MotionCost` from `motion_cost()`. If some observations should reach the LLM, list them in `context_observations`.
4. Use `backend.sleep()`, never `time.sleep`. In motion loops, check `result.orphaned()` and always end with `StopMove()`.
5. Run `uv run go2-dispatch catalog`, run the skill by hand (below), and add tests (contract test in `tests/integration/test_skills.py`, policy test in `tests/unit/test_skill_policies.py`). The catalog golden file `tests/golden/catalog.txt` and the registry hash change. Rewrite the golden file with `uv run pytest --update-golden` and review the diff.
6. Update this page and `docs/decisions.md`.

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

Without `GO2_STUB_STATE_FILE` and `GO2_STUB_TIME_SCALE`, the stub uses `runs/.stub_state.json` (relative to the cwd) and 0.1. On the real robot, set `GO2_BACKEND=real GO2_IFACE=<nic>` (and `GO2_YOLO_WEIGHTS` for detection), with the robot supervised. Pass the full params: skills do not fill defaults. Stdout is the one response line; diagnostics are on stderr.
