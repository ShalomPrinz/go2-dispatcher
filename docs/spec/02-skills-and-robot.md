# Go2 LLM Dispatcher v1 — Part 2 — Skills and robot

This is one part of the Go2 LLM Dispatcher v1 specification. The four parts together are the complete, normative spec. Section numbers (§) are global across the four files, so a reference like §14.4 may point to another part.

| File | Contents |
|---|---|
| `docs/spec/01-foundations.md` | §1–§6: scope, overview, glossary, repository layout, configuration, data models |
| `docs/spec/02-skills-and-robot.md` | §7–§10: skill contract, the five skills and two utilities, real and stub backends, registry and catalog |
| `docs/spec/03-dispatcher.md` | §11–§18: context, LLM client, validation and budgets, executor and stop path, dispatcher loop, transports, run log, failure handling |
| `docs/spec/04-tests-docs-decisions.md` | §19–§23 and Appendix A: tests, open decisions, documentation, build order, v2 seams and future ideas, design review answers |

Conventions (apply to all parts):

- **(sketch)** marks a starting value expected to be tuned. Implement every such value as a config key or a named class/module constant — never inline.
- "Must", "never", "always" are requirements. Everything else is guidance.
- If two parts of the spec seem to conflict, the more specific section wins. §20 (Open decisions) states the default to implement for each open item. Appendix A records the answers to the design review and is normative.

---

## 7. Skills: contract, SKILL.md format, policies, shared helpers

A skill consists of:

1. `skills/<name>/SKILL.md` — YAML frontmatter (read by the registry) plus human prose (never sent to the LLM).
2. `src/go2_skills/<name>.py` — a module with a `main()` that runs in a subprocess, and a module-level `POLICY` object read by the dispatcher.

### 7.1 SKILL.md frontmatter

Frontmatter is the YAML block between the first two lines consisting of `---` at the top of the file, parsed with `yaml.safe_load`. Allowed keys (any other key is a registry error):

| Key | Type | Required | Meaning |
|---|---|---|---|
| `name` | string matching `^[a-z][a-z0-9_]*$` | yes | Must equal the folder name and `POLICY.name`. |
| `entrypoint` | string | yes | Module run with `python -m`, e.g. `go2_skills.walk`. |
| `description` | non-empty string, one sentence on a single line | yes | Goes into the catalog (which is line-based). |
| `params` | mapping name → ParamSpec | no | Declared parameters, in catalog order. Absent = no parameters. |
| `expect` | any | no | Reserved for v2. Accepted and ignored. |

ParamSpec keys (any other key is a registry error):

| Key | Type | Applies to | Meaning |
|---|---|---|---|
| `type` | `number` \| `integer` \| `string` \| `enum` | all | Required. |
| `description` | non-empty string | all | Required. Short. |
| `values` | non-empty list of unique, lowercase, already-stripped strings | `enum` | Required for `enum`, forbidden otherwise. |
| `min`, `max` | finite number (not a boolean) | `number`, `integer` | Optional, inclusive. `min <= max` if both. Forbidden on other types. |
| `default` | value of the param's type | all | Optional. If absent the param is required. The default must pass the param's own checks. |
| `unit` | non-empty string | `number`, `integer` | Optional. Shown in the catalog. Forbidden on other types. |

Parameter names must match `^[a-z][a-z0-9_]*$`. Default checks: `number` = finite int/float, not a boolean; `integer` = int, not a boolean (YAML `2.0` is rejected; declared defaults are not converted); `string` = non-empty after stripping; `enum` = exactly one of `values`; numeric defaults within `min`/`max`. `default: null` is invalid. `params:` with no value (or any non-mapping) is an error.

The markdown body below the frontmatter documents the skill in prose for humans. It is not parsed.

### 7.2 Policy objects

`go2_skills/policy_base.py` (no third-party imports; re-exported by `go2_dispatcher/policies.py`):

```python
@dataclass(frozen=True)
class MotionCost:
    distance_m: float = 0.0
    rotation_deg: float = 0.0

class SkillPolicy:
    name: str = ""
    context_observations: tuple[str, ...] = ()   # observation keys shown to the LLM on ok

    def timeout_s(self, params: dict) -> float:
        raise NotImplementedError

    def motion_cost(self, params: dict) -> MotionCost:
        return MotionCost()
```

Rules:

- Each skill module defines `class <Name>Policy(SkillPolicy)` and `POLICY = <Name>Policy()`.
- Every number used by a policy is a **class attribute** (for example `BASE_S`, `FACTOR`, `TIMEOUT_S`), so it can be tuned in one place and monkeypatched in tests.
- `params` passed to a policy are filled, normalised and bounds-checked.
- The policy lives next to the skill code so formulas share constants with it (for example the walking velocity). `go2_skills` never imports `go2_dispatcher`.
- Importing a skill module must have no side effects: no SDK import, no DDS init, no argument parsing, no output. All work happens in `main()`, guarded by `if __name__ == "__main__": main()`. Module top level may import only the standard library and `go2_skills` modules.

### 7.3 Invocation contract

```
python -m go2_skills.<name> '<params-json>'
```

- `argv[1]` is a JSON object with the filled params. Always present (`{}` for no params).
- Environment variables set by the executor (§14.2) select the backend and carry stub settings.
- Output: **exactly one** JSON line on the real stdout, written by `result.emit()`. Everything else goes to stderr.
- Exit code 0 when `status == "ok"`, 1 when `status == "error"`. Any other exit means no valid response.
- Skills do minimal parameter checking of their own (presence, type, enum membership) and return `invalid_params` on violations, so they are safe to run by hand. Ranges are enforced only by the dispatcher.
- Skills apply **no parameter defaults**: they expect filled params (defaults are filled by the dispatcher, §13.2). A missing parameter, including one that has a `default` in SKILL.md (e.g. `walk` without `distance_m`), returns `invalid_params`. Numbers must be finite and not booleans; ints are accepted. Unknown extra keys are ignored. Enum values must match exactly (the dispatcher normalises case); only `detect_object.target` is stripped and lowercased (§8.5).

### 7.4 `go2_skills/result.py`

```python
def capture_stdout() -> None:
    """Call first in main(). Duplicate fd 1 to a saved fd, then dup2 fd 2 onto fd 1,
    so anything printed by the SDK, cyclonedds, ultralytics or C code goes to stderr.
    emit() writes only to the saved fd."""

def start_orphan_watchdog() -> None:
    """Start a daemon thread that polls os.getppid() every 0.2 s. If it differs from
    int(os.environ["GO2_PARENT_PID"]), set the module flag ORPHANED; if the process is
    still alive 2.0 s later, os._exit(137). Motion loops check orphaned() each iteration
    and break (then StopMove). Does nothing if GO2_PARENT_PID is unset (manual runs)."""

def orphaned() -> bool: ...

def write_raw_stdout(text: str) -> None:
    """Write text to the saved stdout fd. Used only by the stub's `garbage` fault."""

def build_response(skill, status, *, observations=None, error_code=None, error_message=None,
                   state_before=None, state_after=None, state_error=None, timing=None) -> dict:
    """Pure function returning the SkillResponse dict (schema_version=1).
    status == "error" requires error_code and error_message (ValueError otherwise).
    error_message: newlines replaced by spaces, collapsed, cut to 300 chars."""

def emit(skill, status, **kwargs) -> NoReturn:
    """line = json.dumps(build_response(...), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    Write line + "\n" to the saved stdout fd, os.fsync it, then os._exit(0 if ok else 1).
    os._exit is required: DDS/SDK threads can hang normal interpreter shutdown."""

Body = Callable[[dict], tuple[str, dict, str | None, str | None, dict]]
# returns (status, observations, error_code, error_message, timing) where timing has init_ms, exec_ms

def run_skill(skill: str, body: Body, *, sample_state: bool = True) -> NoReturn:
    """Standard main():
    1. capture_stdout(); start_orphan_watchdog(); t0 = monotonic()
    2. params = json.loads(argv[1]); must be a dict -> else emit error invalid_params
    3. if sample_state: state_before = backend.sample_state() (exception -> state_error, continue)
    4. status, obs, code, msg, timing = body(params)
    5. if sample_state: state_after = backend.sample_state() (exception -> append to state_error)
    6. timing["state_ms"] = total time spent in the two samples; timing["total_ms"] = since t0
    7. emit(...)
    Any exception from steps 2–5 is caught: status=error, code="exception",
    message="<ExceptionType>: <first line of str(e)>"; the traceback goes to stderr."""
```

Timing keys: `init_ms` (client construction and init), `exec_ms` (the action, including any settle wait), `state_ms` (state sampling), `total_ms` (whole `main`). Measured with `time.monotonic()`.

---

## 8. The five skills and two utilities

Common rules for skills that call the SDK:

- Keep the original motion logic: constant velocity, a 10 Hz command loop, `StopMove()` at the end.
- Any SDK call returning a non-zero code: stop the action, call `StopMove()` for motion skills, and return `status=error`, `code=sdk_error`, `message="<Call> returned <code>"`.
- `sdk_ret` observation = return code of the last SDK call.
- Skills never call `time.sleep`; they call `backend.sleep()` (§9).

### 8.1 walk

`skills/walk/SKILL.md` frontmatter:

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

Module constants: `VELOCITY_MPS = 0.3`, `CMD_PERIOD_S = 0.1`, `SDK_TIMEOUT_S = 10.0`.

Behaviour:

1. Validate `direction` ∈ enum and `distance_m` is a number → else `invalid_params`.
2. `client = backend.get_sport_client()` (timing: `init_ms`).
3. `duration = distance_m / VELOCITY_MPS`; `n = max(1, round(duration / CMD_PERIOD_S))`.
4. Velocity: forward `(+v, 0, 0)`, backward `(-v, 0, 0)`, left `(0, +v, 0)`, right `(0, -v, 0)`.
5. Repeat `n` times: if `result.orphaned()`: break; `ret = client.Move(vx, vy, 0)`; non-zero → StopMove, `sdk_error`; `backend.sleep(CMD_PERIOD_S)`.
6. `ret = client.StopMove()`; non-zero → `sdk_error`.
7. Observations: `direction`, `distance_m`, `duration_s` (= `n × CMD_PERIOD_S`, the commanded duration), `sdk_ret`, and `orphaned: true` only if it broke out for that reason.

Policy:

```python
class WalkPolicy(SkillPolicy):
    name = "walk"
    BASE_S = 10.0     # process start + SDK init + state samples (sketch)
    FACTOR = 1.5      # safety factor on commanded motion time (sketch)
    def timeout_s(self, p): return self.BASE_S + self.FACTOR * p["distance_m"] / VELOCITY_MPS
    def motion_cost(self, p): return MotionCost(distance_m=p["distance_m"])
```

Error codes: `invalid_params`, `sdk_error`, `backend_not_configured`, `exception`.

### 8.2 turn

**Units: degrees** in the interface; converted to radians inside the skill.

```yaml
---
name: turn
entrypoint: go2_skills.turn
description: Turn in place to the left or right by an angle, then stop.
params:
  direction:
    type: enum
    values: [left, right]
    description: Which way to turn.
  angle_deg:
    type: number
    min: 5
    max: 180
    default: 45
    unit: degrees
    description: Angle to turn.
---
```

Module constants: `YAW_RATE_RPS = 1.0` (rad/s), `CMD_PERIOD_S = 0.1`, `SDK_TIMEOUT_S = 10.0`.

Behaviour: as walk, with `duration = radians(angle_deg) / YAW_RATE_RPS`, command `Move(0, 0, +rate)` for left and `Move(0, 0, -rate)` for right. Observations: `direction`, `angle_deg`, `duration_s`, `sdk_ret` (+ `orphaned`).

Policy: `BASE_S = 10.0`, `FACTOR = 1.5` (sketch); `timeout_s = BASE_S + FACTOR * radians(angle_deg) / YAW_RATE_RPS`; `motion_cost = MotionCost(rotation_deg=angle_deg)`.

### 8.3 sit

```yaml
---
name: sit
entrypoint: go2_skills.sit
description: Lower the robot's body to the ground (sit or lie down).
---
```

Behaviour: `ret = client.StandDown()` (unchanged from the original, which uses `StandDown`, not `Sit`). Non-zero → `sdk_error`. Then `backend.sleep(SitPolicy.SETTLE_S)` before returning, so `state_after` is sampled after the motion finishes (SDK action calls return when the command is accepted, not when the motion ends).

Observations: `sdk_ret`. Policy: `TIMEOUT_S = 15.0`, `SETTLE_S = 3.0` (sketch; OD-10); zero motion cost.

### 8.4 stretch

```yaml
---
name: stretch
entrypoint: go2_skills.stretch
description: Perform the robot's built-in stretch routine.
---
```

Behaviour: `ret = client.Stretch()`; non-zero → `sdk_error`; then `backend.sleep(StretchPolicy.SETTLE_S)`. Observations: `sdk_ret`. Policy: `TIMEOUT_S = 20.0`, `SETTLE_S = 6.0` (sketch; OD-10); zero motion cost.

### 8.5 detect_object

```yaml
---
name: detect_object
entrypoint: go2_skills.detect_object
description: Look through the front camera once and report whether an object is visible, where it is in the frame, and roughly how close it is. Does not move the robot.
params:
  target:
    type: string
    description: Object to look for, as a lowercase COCO class name (for example chair, person, bottle, cell phone).
---
```

The 80-class list is **not** in the SKILL.md or the catalog. It lives in `go2_skills/coco.py` as `COCO_CLASSES: tuple[str, ...]`, copied exactly and in order from the original `detect_object/SKILL.md`.

Behaviour:

1. `target = params["target"].strip().lower()`; missing or not a non-empty string → `invalid_params`.
2. `target not in COCO_CLASSES` → `status=error`, `code=unsupported_object`, message `"'<target>' is not a detectable object. Closest supported: <a>, <b>."` from `difflib.get_close_matches(target, COCO_CLASSES, n=3, cutoff=0.5)`; if there are no matches, `"'<target>' is not a detectable object."`. No backend call is made.
3. `result = backend.get_detector().detect(target)`.
4. Real detector: `VideoClient()`, `SetTimeout(3.0)`, `Init()`, `GetImageSample()` → decode with `cv2.imdecode` → YOLO (`weights = os.environ["GO2_YOLO_WEIGHTS"]`, `imgsz=640`, `conf=0.4`, `verbose=False`) → keep boxes whose class name equals `target` → most confident → position by box centre x fraction (`< 0.4` left, `> 0.6` right, else center) → closeness by box height fraction (`> 0.6` near, `> 0.3` medium, else far). Same thresholds as the original.
5. Errors: camera non-zero code or empty data → `camera_unavailable`; decode failure → `bad_frame`; weights file missing → `weights_missing` (never trigger an automatic download).
6. Not seen: **`status=ok`**, observations `{"target": t, "object_found": false}`. Not finding the object is not an error.
7. Seen: `status=ok`, observations `{"target", "object_found": true, "position", "closeness", "confidence"}`, confidence rounded to 2 decimals.

Policy: `TIMEOUT_S = 45.0` (sketch; YOLO load on CPU); zero motion cost; `context_observations = ("object_found", "position", "closeness", "confidence")`.

### 8.6 stop_move (utility)

`python -m go2_skills.stop_move '{}'`. Not in `skills/`, not in the registry, never visible to the LLM.

1. `capture_stdout()`; `t0 = monotonic()`. No orphan watchdog (it must finish even if the parent died).
2. `client = backend.get_sport_client()`; `ret = client.StopMove()`; `timing.stop_call_ms = (monotonic() - t0) × 1000` measured right after `StopMove()` returns.
3. `backend.sleep(0.5)`, then `state_after = backend.sample_state()` (exception → `state_error`).
4. Emit `skill="stop_move"`, `status=ok` if `ret == 0` else `error` with `sdk_error`; observations `{"sdk_ret": ret}`.
5. The stub ignores faults for this utility.

### 8.7 read_state (utility)

`python -m go2_skills.read_state '{}'`. Samples state once and emits `skill="read_state"`, `status=ok`, `state_after=<state>`; on failure `status=error`, `code=state_unavailable`. No faults, no watchdog. Used at dispatcher startup on the real backend (§15.1) and by `go2-dispatch state`.

---

## 9. Robot backend: real, stub, state sampling

`go2_skills/backend.py` dispatches on env `GO2_BACKEND` (`real` | `stub`). A missing or unknown value raises `BackendNotConfigured` (so does the real backend when `GO2_IFACE` is empty), which `run_skill` and both utilities always map to `status=error`, `code=backend_not_configured`, without a traceback on stderr. It is never reported as a `state_error` or `exception`, even when raised while sampling state. The executor always sets it.

```python
def get_sport_client():     # object with Move, StopMove, StandDown, Stretch -> int
def get_detector():         # object with detect(target) -> DetectResult
def sample_state() -> dict  # RobotState as a plain dict
def sleep(seconds: float)   # real: time.sleep(seconds); stub: time.sleep(seconds * time_scale)

@dataclass
class DetectResult:
    found: bool
    position: str | None = None
    closeness: str | None = None
    confidence: float | None = None
```

`go2_skills/posture.py`:

```python
POSTURE_SITTING_MAX_M = 0.15     # (sketch; OD-2)
POSTURE_STANDING_MIN_M = 0.22    # (sketch; OD-2)

def derive_posture(body_height: float | None, mode: int | None) -> str:
    """v1 rule: body_height < SITTING_MAX -> "sitting"; >= STANDING_MIN -> "standing";
    otherwise or None -> "unknown". `mode` is accepted but unused in v1, so the rule can
    change after the robot checklist without touching callers."""
```

### 9.1 Real backend (`go2_skills/real.py`)

- `_init_dds()`: once per process, `ChannelFactoryInitialize(0, os.environ["GO2_IFACE"])`, guarded by a module flag. Called by every public function before touching DDS, including `sample_state()`.
- `get_sport_client()`: `SportClient()`, `SetTimeout(10.0)`, `Init()`; returned as-is.
- `get_detector()`: wraps `VideoClient` + YOLO (§8.5). YOLO is loaded on first `detect()`.
- `sample_state()`:
  - On the first call, create `ChannelSubscriber("rt/sportmodestate", SportModeState_)` with a callback that stores `(local_arrival_monotonic, msg)`; `Init(callback, 10)`.
  - Wait up to 1.0 s for a message whose local arrival time is later than the moment `sample_state()` was called. If none arrives, use the latest earlier message if one exists; otherwise raise `StateUnavailable`.
  - Map fields: `mode`, `gait_type`, `body_height`, `position` (3), `velocity` (3), `yaw_speed`, `imu_state.rpy` → `imu_rpy`, `foot_force` (4), `error_code`. A missing attribute becomes `None`. Convert numpy/ctypes values to plain Python numbers. Any non-finite float (NaN, ±inf) becomes `None`.
  - `posture = derive_posture(body_height, mode)`; `backend = "real"`; `t = time.time()`.
- All third-party imports are inside functions: `unitree_sdk2py.core.channel` (`ChannelFactoryInitialize`, `ChannelSubscriber`), `unitree_sdk2py.go2.sport.sport_client.SportClient`, `unitree_sdk2py.go2.video.video_client.VideoClient`, `unitree_sdk2py.idl.unitree_go.msg.dds_.SportModeState_`, `cv2`, `numpy`, `ultralytics.YOLO`.

### 9.2 Stub backend (`go2_skills/stub.py`)

Purpose: run the whole pipeline with no robot, and exercise failure paths. It replaces **only** the SDK layer inside the skill subprocess, so the process, timeout and kill mechanisms run for real.

Environment read by the stub:

| Env var | Meaning |
|---|---|
| `GO2_STUB_STATE_FILE` | Absolute path of the JSON state file shared across processes. |
| `GO2_STUB_TIME_SCALE` | Float multiplying every simulated duration. |
| `GO2_STUB_DETECTIONS` | JSON object `{class: "position:closeness"}`. |
| `GO2_STUB_FAULT` | `error` \| `hang` \| `crash` \| `garbage`; set only for the faulted step. |
| `GO2_STUB_NOISE` | Test-only. If `1`, the stub writes junk to stdout via `print()` and `os.write(1, ...)` on first use. |

State file: `{"posture": "standing" | "sitting"}`. Read on each call; written by writing a temp file in the same folder and `os.replace`. Missing file → `standing`.

`StubSportClient` (every method returns `int`, 0 = success):

| Call | While standing | While sitting |
|---|---|---|
| `Move(vx, vy, vyaw)` | 0 | `STUB_ERR_NOT_STANDING = 1` |
| `StopMove()` | 0 | 0 |
| `StandDown()` | 0; posture → sitting; `sleep(1.5)` | 0; no change |
| `Stretch()` | 0; `sleep(3.0)` | `STUB_ERR_NOT_STANDING` |

(All sleeps through `backend.sleep`, i.e. scaled.)

`StubDetector.detect(target)`: `sleep(0.5)`; if `target` is in `GO2_STUB_DETECTIONS`, return found with that position/closeness and `confidence=0.9`; else not found.

`sample_state()` (stub): `{"t": time.time(), "backend": "stub", "posture": <state file>, "body_height": 0.32 if standing else 0.08}`; other fields `None`.

Fault injection — applied to the **first action call** a skill makes (`Move`, `StopMove`, `StandDown`, `Stretch`, or `detect`). `sample_state()` and client construction are never faulted. Utilities ignore faults.

| Kind | Behaviour | Expected step outcome |
|---|---|---|
| `error` | The call returns `STUB_ERR_INJECTED = 99` (detector: raises `StubCameraError`, mapped by the skill to `camera_unavailable`) | `error` |
| `hang` | The call sleeps forever (`while True: time.sleep(1)`, unscaled) | `timeout` |
| `crash` | `os._exit(139)` without writing anything | `malformed` |
| `garbage` | Writes `not json\n` to the **saved** stdout fd (via `result.write_raw_stdout()`), then `os._exit(0)` | `malformed` |

### 9.3 Stub state lifecycle

- Reset to `stub.initial_posture` once at startup by `run`, `batch` and `go2-bot` (stub backend only).
- Not reset between tasks — the robot remembers.
- `go2-dispatch --reset-stub` resets it and exits. With the real backend it is an error (exit 2).

---

## 10. Registry and skill catalog

`go2_dispatcher/registry.py`:

```python
@dataclass(frozen=True)
class ParamSpec:
    type: str; description: str
    values: tuple[str, ...] | None = None
    min: float | None = None; max: float | None = None
    default: Any = MISSING; unit: str | None = None

@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    entrypoint: str
    description: str
    params: dict[str, ParamSpec]      # frontmatter order
    policy: SkillPolicy

class Registry:
    @classmethod
    def load(cls, skills_dir: Path) -> "Registry"   # raises RegistryError
    def get(self, name: str) -> SkillDescriptor | None
    def names(self) -> list[str]                     # sorted
    def catalog_text(self) -> str
```

Loading rules:

- Every direct subfolder of `skills_dir` containing `SKILL.md` is a skill. Other subfolders and top-level files are ignored.
- `RegistryError` naming the file for: missing or invalid frontmatter, unknown key, missing required key, `name` ≠ folder name, invalid ParamSpec, default failing its own checks, entrypoint not importable, module without `POLICY`, `POLICY.name` ≠ `name`, duplicate names. Zero skills is also a `RegistryError`, as is a missing `skills_dir` (naming the directory).
- Stricter checks (beyond the tables in §7.1), each a `RegistryError`: frontmatter must start on the first line with exactly `---` (trailing whitespace tolerated) and be closed by the next `---` line; empty, non-mapping or invalid YAML; `params` present but not a mapping; parameter name not matching `^[a-z][a-z0-9_]*$`; skill or param `description` not a non-empty string, or a skill description spanning more than one line; duplicate or unstripped enum `values`; `min`/`max` not finite numbers or booleans; empty `unit`; `min`/`max`/`unit` on a non-numeric type (like `values` on a non-enum); `default: null`; `POLICY` not a `SkillPolicy` instance. Any exception while importing the entrypoint is reported as "not importable".
- Transports print `Registry error: <message>` and exit with code 2.

Catalog rendering is deterministic: skills sorted by name, params in frontmatter order, numbers formatted with `f"{v:g}"`. Per skill:

```
{name}: {description}
  - {param} (required): {type phrase}. {param description}
  - {param} (optional, default {default}): {type phrase}. {param description}
```

or `  - no parameters`. No blank lines between skills. Type phrases:

- `enum`: `one of a, b, c`
- `number`: `number from {min} to {max} {unit}` / `number, at least {min} {unit}` / `number, at most {max} {unit}` / `number {unit}` — trailing unit omitted when absent, no trailing space.
- `integer`: same with `integer`.
- `string`: `text`

The exact expected catalog for the five skills (this is the golden file `tests/golden/catalog.txt`):

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

Registry hash: `sha256(system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True))`, hex, first 16 characters. It identifies the exact prompt surface of a run and is logged in `task_start` and `index.jsonl`.
