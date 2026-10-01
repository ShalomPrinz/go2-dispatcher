# Go2 LLM Dispatcher v1 — Part 1 — Foundations

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

## 1. Purpose and scope

### 1.1 What v1 is

A Python program that:

1. Receives a natural-language task from an operator (Telegram or CLI).
2. Asks an LLM for a **plan** — an ordered list of skill calls — returned through a single forced tool named `submit_plan`.
3. Validates the plan (schema, horizon, parameter bounds, motion budget).
4. Runs each step as a separate subprocess that drives a Unitree Go2 EDU (or a stub).
5. Feeds structured results back to the LLM, which decides: finished (`DONE`), more work (`PLAN`), or cannot be done (`ABORT`).
6. Enforces limits: failure budget, LLM-call budget, task time limit, per-step timeouts, per-task motion budget, and an operator `stop` word.
7. Writes a complete JSONL run log per task. The log is the dataset for a later study; tokens, latency and replanning must be measurable from it.

### 1.2 Why this shape

- The plan is a first-class object, so it can be logged, counted and inspected.
- Context is assembled from fixed slots rather than an accumulating conversation, so token and latency measurements are comparable across experimental conditions. Context size depends only on the current task.
- Skills run as one process per call, because the Unitree SDK initialises its DDS channel through a process-wide singleton. A fresh process guarantees a clean channel and lets a hung call be killed.

### 1.3 In scope for v1

- Dispatcher loop: plan → validate → execute → feed back.
- Five skills: `walk`, `turn`, `sit`, `stretch`, `detect_object`. The old `walk` is split into `walk` (straight line) and `turn` (in place).
- Two utilities that are not skills: `stop_move` and `read_state`.
- One common skill response format produced by a shared helper.
- A stub backend that remembers sitting/standing and supports fault injection.
- Robot state sampled by each skill at start and end, **logged only**, plus a derived posture that is shown to the LLM.
- Bounds checking, per-task motion budget, failure budget, LLM-call budget, task time limit, per-skill timeouts.
- Operator `stop` word that bypasses busy-reject, kills the running skill and sends `StopMove` from a fresh process. The same path is used for step timeouts and the task time limit.
- Infrastructure retries for LLM calls, logged separately.
- CLI and Telegram transports over the same dispatcher.
- JSONL run log.
- Tests (§19) and documentation (§21).

### 1.4 Out of scope for v1

| Item | Status |
|---|---|
| Verification (pre/postconditions, verdicts) | v2. `StepResult.verification` exists and is always `"unverified"`. |
| `ASK` plan status | v2. In v1 the model returns `ABORT` with a clarifying question in `message`. |
| Stub that models motion | v2 prerequisite. v1 stub remembers posture only. |
| Forwarding mid-run messages to the LLM | Future idea. v1 recognises only `stop`. |
| Prompt caching | Future idea. Do not enable. |
| Queueing, multiple operators, streaming | Not planned. |
| Other skill sets (granularity tiers) | Later. v1 must not prevent them: the skills folder is configurable. |
| OpenClaw baseline experiment | Later. Nothing in v1 depends on OpenClaw. |

---

## 2. System overview

```
 Operator ──► Transport (Telegram | CLI)
                │  run_task(text) / request_stop() / shutdown()
                ▼
           Dispatcher ─────────────────────► Run log (runs/*.jsonl, runs/index.jsonl)
            │    ▲
   context  │    │ plan
            ▼    │
       LLM client (Anthropic Messages API, forced tool submit_plan)
            │
            ▼
  Plan validation ─► bounds ─► motion budget
            │
            ▼
        Executor ── one subprocess per step ──► python -m go2_skills.<skill>
            ▲                                          │
            │   one JSON line (SkillResponse)          ▼
            └────────────────────────────── Backend: real SDK | stub
```

- The dispatcher never touches DDS or the SDK. Only skill and utility processes do.
- The dispatcher imports `go2_skills` modules only to read their **policy objects** (timeout formula, motion cost, context observations). Importing a skill module must never import the SDK.
- Only one dispatcher process may run per machine (§5.4).

---

## 3. Glossary

| Term | Meaning |
|---|---|
| Task | One operator message carried out from receipt to a final outcome. |
| Plan | One `submit_plan` reply: `status`, `steps`, optional `replan_after`, `message`. |
| Step | One skill call inside a plan. |
| LLM call | One request to the LLM that produced a response (valid or not). Infra retries are not separate calls. |
| Return reason | Why the dispatcher is calling the LLM: `initial`, `plan_complete`, `checkpoint`, `failure`, `schema_retry`. |
| Failure | A step outcome of `error`, `timeout`, `malformed`, `rejected`, or `motion_budget_exceeded`. |
| Horizon | Maximum number of steps allowed in one plan. |
| Checkpoint | `replan_after = N`: the model asks to be called again after step N of its plan. |
| Posture | `standing`, `sitting`, or `unknown`, derived from sampled robot state. |
| Stop path | Kill the running skill process, then send `StopMove` from a fresh process. |
| Base dir | The folder that relative config paths resolve against and the subprocess working directory (§5.3). |

---

## 4. Repository layout and packaging

New repository. Nothing is forked from `TamirAshwal/Go2`; skill logic is ported.

```
go2-dispatcher/
├── pyproject.toml
├── uv.lock
├── config.example.toml          # committed; users copy to config.toml
├── .env.example                 # ANTHROPIC_API_KEY=, TELEGRAM_BOT_TOKEN=
├── .gitignore                   # config.toml, .env, runs/, models/, .venv/, __pycache__/
├── README.md                    # one paragraph + quick start + links into docs/
├── skills/                      # skill set loaded by the registry (config skills.dir)
│   ├── detect_object/SKILL.md
│   ├── sit/SKILL.md
│   ├── stretch/SKILL.md
│   ├── turn/SKILL.md
│   └── walk/SKILL.md
├── src/
│   ├── go2_dispatcher/
│   │   ├── __init__.py          # __version__ = "0.1.0"
│   │   ├── config.py            # config models, loading, path resolution, .env parser
│   │   ├── clock.py             # Clock protocol, MonotonicClock
│   │   ├── models.py            # Plan, StepResult, TaskOutcome, ..., exceptions
│   │   ├── policies.py          # re-exports SkillPolicy, MotionCost from go2_skills.policy_base
│   │   ├── registry.py          # SKILL.md loading, catalog rendering, registry hash
│   │   ├── prompts.py           # system text, notices, operator messages (all fixed text)
│   │   ├── render.py            # step-line rendering shared by context and transports
│   │   ├── context.py           # request assembly
│   │   ├── llm.py               # AnthropicPlanner, tool schema, infra retries
│   │   ├── validation.py        # plan-level validation
│   │   ├── bounds.py            # step parameter validation + default filling
│   │   ├── budget.py            # MotionBudget
│   │   ├── executor.py          # subprocesses, kill, StopMove, read_state
│   │   ├── dispatcher.py        # loop, busy lock, stop, shutdown
│   │   ├── runlog.py            # RunLogFactory, RunLog, index writer
│   │   ├── process_lock.py      # machine-wide single-instance lock
│   │   └── transports/
│   │       ├── __init__.py      # build_dispatcher(), format_outcome()
│   │       ├── cli.py
│   │       └── telegram_bot.py
│   └── go2_skills/
│       ├── __init__.py
│       ├── policy_base.py       # SkillPolicy, MotionCost (no third-party imports)
│       ├── result.py            # stdout capture, emit(), run_skill(), orphan watchdog
│       ├── backend.py           # get_sport_client(), get_detector(), sample_state(), sleep()
│       ├── posture.py           # derive_posture(state) — single place for the rule
│       ├── stub.py              # StubSportClient, StubDetector, state file, faults
│       ├── real.py              # real SDK wrappers (lazy imports)
│       ├── coco.py              # COCO_CLASSES (80 names)
│       ├── walk.py  turn.py  sit.py  stretch.py  detect_object.py
│       ├── stop_move.py         # utility
│       └── read_state.py        # utility
├── tests/
│   ├── conftest.py
│   ├── golden/                  # golden files for catalog and context
│   ├── helpers/                 # ScriptedPlanner, FakeClock, FakeExecutor, fake Telegram objects
│   ├── unit/
│   ├── integration/
│   └── robot/                   # opt-in
├── docs/                        # §21
├── runs/                        # run logs (gitignored)
└── models/                      # YOLO weights (gitignored)
```

### 4.1 `pyproject.toml`

```toml
[project]
name = "go2-dispatcher"
version = "0.1.0"
requires-python = ">=3.10,<3.12"          # see OD-3
dependencies = [
  "anthropic",                            # current major; pin in uv.lock
  "httpx",
  "pydantic>=2",
  "PyYAML",
  "python-telegram-bot>=21",
  "tomli; python_version < '3.11'",
]

[project.optional-dependencies]
robot  = ["unitree_sdk2py", "cyclonedds==0.10.2"]
vision = ["ultralytics", "opencv-python", "numpy"]

[project.scripts]
go2-dispatch = "go2_dispatcher.transports.cli:main"
go2-bot      = "go2_dispatcher.transports.telegram_bot:main"

[dependency-groups]
dev = ["pytest", "pytest-timeout"]

[tool.uv.sources]
unitree_sdk2py = { git = "https://github.com/unitreerobotics/unitree_sdk2_python" }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/go2_dispatcher", "src/go2_skills"]

[tool.pytest.ini_options]
testpaths = ["tests"]
timeout = 30
markers = [
  "integration: spawns real subprocesses with the stub backend",
  "live_llm: calls the real Anthropic API (needs --run-live and ANTHROPIC_API_KEY)",
  "robot: needs the real Go2 (needs --run-robot)",
]
```

Rules:

- No `sys.path.insert` anywhere. No absolute paths in code. No hardcoded network interface.
- Stub mode and the default test suite must work with only core + dev dependencies. `unitree_sdk2py`, `cyclonedds`, `ultralytics`, `cv2`, `numpy` are imported lazily, only inside real-backend code paths.
- Commands: `uv sync` (core + dev); on the lab machine `uv sync --extra robot --extra vision`.
- M1 must verify that `uv lock` succeeds on a clean machine without CycloneDDS installed. If it fails because `cyclonedds` cannot be resolved without building, remove the `robot` extra from `pyproject.toml` and document a manual install instead: `uv pip install cyclonedds==0.10.2` and `uv pip install -e <path to unitree_sdk2_python>`, after which the lab machine uses `uv sync --inexact` (so `uv sync` does not remove them). Record which path was taken in `docs/decisions.md`.

---

## 5. Configuration and paths

### 5.1 Sources and precedence

1. Defaults in `config.py` (pydantic models).
2. `config.toml`, path from `--config` (default `./config.toml`). If the default file is missing, defaults are used and a one-line warning is printed to stderr. If an explicit `--config` path is missing, that is a config error.
3. CLI flags that override specific keys (§16.2).

Secrets come only from environment variables:

- `ANTHROPIC_API_KEY` — required only when a real LLM client is constructed (`run`, `batch`, `go2-bot`, and not when the test planner hook is used).
- `TELEGRAM_BOT_TOKEN` — required only for `go2-bot`.

A `.env` file in the base dir is loaded by a small built-in parser (do not add `python-dotenv`): one `KEY=VALUE` per line; optional leading `export `; blank lines and lines starting with `#` ignored; one pair of matching surrounding single or double quotes stripped from the value; no interpolation. **Real environment variables win** over `.env` values.

Secrets are never written to the run log, never printed, and never passed to child processes (§14.2).

All config models use `model_config = ConfigDict(extra="forbid")`. Any config error (unknown key, invalid value, missing file given explicitly) prints `Config error: <message>` to stderr and exits with code **2**.

### 5.2 Full config reference

```toml
[run]
condition = ""                      # free-text experiment label, copied into task_start and index.jsonl

[llm]
model = "claude-sonnet-5-5"         # (sketch)
max_tokens = 1024
temperature = 0.0
request_timeout_s = 60.0            # cap per attempt (sketch)
infra_max_retries = 2
infra_backoff_s = [1.0, 4.0]        # sleep before retry 1, retry 2

[loop]
planning_horizon = 5                # max steps per plan (sketch)
max_failures = 3                    # task ends when failures reach this (sketch)
max_llm_calls = 20                  # per task (sketch)
task_time_limit_s = 300.0           # wall clock per task (sketch)
context_history_k = 10              # executed entries shown in full (sketch)

[motion_budget]
max_distance_m = 10.0               # commanded travel per task (sketch)
max_rotation_deg = 720.0            # commanded rotation per task (sketch)

[skills]
dir = "skills"

[robot]
backend = "stub"                    # "stub" | "real"
network_interface = ""              # required when backend = "real", e.g. "enp0s31f6"
yolo_weights = "models/yolov8n.pt"
stop_move_timeout_s = 10.0
read_state_timeout_s = 10.0

[stub]
time_scale = 0.1                    # multiplies every simulated duration
initial_posture = "standing"        # "standing" | "sitting"
state_file = "runs/.stub_state.json"
detections = {}                     # e.g. { chair = "center:near", "cell phone" = "left:far" }
faults = []                         # e.g. [ { step = 2, kind = "hang" } ]

[log]
dir = "runs"

[telegram]
allowed_user_ids = []               # numeric Telegram user ids; empty = nobody allowed
```

Validation (each failure is a config error):

- `robot.backend == "real"` requires non-empty `robot.network_interface`.
- `stub.faults` must be empty when `robot.backend == "real"`.
- `stub.faults[].kind` ∈ `error | hang | crash | garbage`; `stub.faults[].step` integer ≥ 1; steps unique.
- `stub.detections` keys ∈ `COCO_CLASSES`; values match `^(left|center|right):(near|medium|far)$`.
- Integers ≥ 1: `planning_horizon`, `max_failures`, `max_llm_calls`, `context_history_k`, `max_tokens`.
- Floats > 0: `task_time_limit_s`, `request_timeout_s`, `stop_move_timeout_s`, `read_state_timeout_s`, `stub.time_scale`.
- Floats ≥ 0: `max_distance_m`, `max_rotation_deg`, `temperature`, every value in `infra_backoff_s`.
- `infra_max_retries` ≥ 0 and `len(infra_backoff_s) >= infra_max_retries`.

### 5.3 Paths

- **Base dir** = the parent folder of the config file if a config file was loaded; otherwise the current working directory.
- Every relative path in config (`skills.dir`, `log.dir`, `stub.state_file`, `robot.yolo_weights`) is resolved against the base dir at load time. The loaded `Config` holds absolute `Path` objects. Absolute paths in config are allowed and kept.
- The `.env` file is looked up in the base dir.
- Every subprocess runs with `cwd = base_dir`.
- `log.dir` is created if missing.

### 5.4 Single instance

`process_lock.acquire(log_dir)` takes an exclusive, non-blocking `fcntl.flock` on `{log.dir}/.dispatcher.lock` and holds it for the life of the process. If it is already held: print `Another dispatcher is running (lock: <path>).` and exit with code 2. Commands that take the lock: `run`, `batch`, `--reset-stub`, `go2-bot`. Commands that do not: `catalog`, `state`. This prevents two processes from driving one robot, clobbering the stub state file, or interleaving `index.jsonl`.

---

## 6. Data models and exceptions

All in `go2_dispatcher/models.py` unless stated. All pydantic v2 models use `extra="forbid"` unless stated.

### 6.1 Plan (LLM output)

```python
class PlanStep(BaseModel):
    skill: StrictStr
    params: dict[str, Any] = {}

class Plan(BaseModel):
    status: Literal["PLAN", "DONE", "ABORT"]
    steps: list[PlanStep] = []
    replan_after: StrictInt | None = None     # 1-based step index within this plan
    message: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_status(cls, data):
        # the API does not guarantee enum casing in non-strict mode
        if isinstance(data, dict) and isinstance(data.get("status"), str):
            data = {**data, "status": data["status"].strip().upper()}
        return data
```

`StrictInt` rejects `"2"`, `2.0` and `True`. The model is deliberately more lenient than the tool schema about `steps` and `params` being present (§12.2). Semantic rules are in §13.1.

### 6.2 SkillResponse (what a skill prints)

The pydantic models here are used by the dispatcher to parse. Skill processes build plain dicts in `go2_skills/result.py` (skills do not import pydantic); a unit test asserts that the dicts validate against these models.

```python
class SkillError(BaseModel):
    code: str
    message: str                               # one line, written for the LLM

class RobotState(BaseModel):
    model_config = ConfigDict(extra="allow")   # tolerate extra fields from the robot
    t: float                                   # unix time when sampled (local clock)
    backend: Literal["real", "stub"]
    posture: Literal["standing", "sitting", "unknown"]
    mode: int | None = None
    gait_type: int | None = None
    body_height: float | None = None
    position: list[float] | None = None        # [x, y, z], robot's own estimate if published
    velocity: list[float] | None = None        # [vx, vy, vz]
    yaw_speed: float | None = None
    imu_rpy: list[float] | None = None         # [roll, pitch, yaw] radians
    foot_force: list[float] | None = None      # 4 values
    error_code: int | None = None

class SkillResponse(BaseModel):
    schema_version: Literal[1]
    skill: str
    status: Literal["ok", "error"]
    observations: dict[str, Any] = {}
    error: SkillError | None = None            # present iff status == "error" (model validator)
    state_before: RobotState | None = None
    state_after: RobotState | None = None
    state_error: str | None = None             # messages joined with "; "
    timing: dict[str, float] = {}              # init_ms, exec_ms, state_ms, total_ms (+ stop_call_ms for stop_move)
```

### 6.3 StepResult (what the dispatcher records)

```python
StepOutcome = Literal["ok", "error", "timeout", "malformed",
                      "rejected", "motion_budget_exceeded", "interrupted"]
FAILURE_OUTCOMES = frozenset({"error", "timeout", "malformed", "rejected", "motion_budget_exceeded"})

class MotionCostModel(BaseModel):              # pydantic mirror of policy_base.MotionCost
    distance_m: float = 0.0
    rotation_deg: float = 0.0

class StopMoveResult(BaseModel):
    ok: bool
    reason: Literal["operator", "task_time_limit", "step_timeout", "shutdown", "internal_error"]
    duration_ms: float
    exit_code: int | None = None
    response: SkillResponse | None = None      # includes state_after sampled after StopMove
    stderr_tail: str | None = None

class StepResult(BaseModel):
    index: int | None                  # 1-based count of dispatched steps in the task; None if not dispatched
    call_index: int                    # LLM call that produced the plan (1-based)
    plan_step: int                     # 1-based position within that plan
    skill: str
    params: dict[str, Any]             # dispatched: filled + normalised params; rejected: raw params as received
    outcome: StepOutcome
    error_code: str | None = None
    error_message: str | None = None   # one line, ≤ 200 chars, safe for LLM context
    response: SkillResponse | None = None
    duration_ms: float = 0.0           # wall clock around the subprocess; 0 if not dispatched
    timeout_s: float | None = None
    motion_cost: MotionCostModel = MotionCostModel()
    fault: str | None = None           # stub fault kind injected, if any
    exit_code: int | None = None
    pid: int | None = None             # LOG ONLY
    stderr_tail: str | None = None     # last 2000 chars; LOG ONLY, never in context
    stop_move: StopMoveResult | None = None
    verification: Literal["unverified"] = "unverified"
```

Outcome meanings:

| Outcome | Dispatched? | Meaning |
|---|---|---|
| `ok` | yes | Valid response with `status == "ok"`. |
| `error` | yes | Valid response with `status == "error"`. |
| `timeout` | yes | Step exceeded its own timeout; process killed; StopMove sent. |
| `malformed` | yes | Process ended by itself without a valid response line. |
| `rejected` | no | A step in the plan failed bounds checks (§13.3). |
| `motion_budget_exceeded` | no | A step would exceed the task's motion budget (§13.3). |
| `interrupted` | yes | Killed by operator stop, task time limit, or shutdown; StopMove sent. |

When the dispatcher killed a process, the kill cause decides the outcome even if a valid response line was printed; that response is still attached for the log. When the process ended by itself, the response decides the outcome; the exit code is only recorded.

### 6.4 Task outcome

```python
TaskOutcomeCode = Literal[
    "DONE", "ABORTED", "STOPPED", "TIME_LIMIT_EXCEEDED",
    "FAILURE_BUDGET_EXHAUSTED", "CALL_BUDGET_EXHAUSTED",
    "LLM_INVALID", "LLM_ERROR", "INTERNAL_ERROR",
]

class TaskOutcome(BaseModel):
    run_id: str
    task: str
    outcome: TaskOutcomeCode
    message: str                        # operator-facing (§11.8)
    steps: list[StepResult]             # every recorded step, in order
    llm_calls: int
    failures: int
    stop_move_failed: bool = False
    duration_ms: float
    final_posture: Literal["standing", "sitting", "unknown"]
    log_path: str
```

| Outcome | When |
|---|---|
| `DONE` | Model returned `DONE`. |
| `ABORTED` | Model returned `ABORT`. |
| `STOPPED` | Operator `stop` (or dispatcher shutdown) during the task. |
| `TIME_LIMIT_EXCEEDED` | `task_time_limit_s` reached. |
| `FAILURE_BUDGET_EXHAUSTED` | Failures reached `max_failures`. |
| `CALL_BUDGET_EXHAUSTED` | `max_llm_calls` reached without `DONE`/`ABORT`. |
| `LLM_INVALID` | A plan and its one retry were both invalid. |
| `LLM_ERROR` | LLM unreachable after infra retries, or a non-retryable API error. |
| `INTERNAL_ERROR` | Unhandled exception in the dispatcher. |

### 6.5 TaskSummary (carried to the next task)

```python
class TaskSummary(BaseModel):
    task: str
    outcome: TaskOutcomeCode
    message: str
    last_step: StepResult | None        # last dispatched step, if any
```

### 6.6 Exceptions

```python
class ConfigError(Exception): ...
class RegistryError(Exception): ...           # message names the offending file
class BusyError(Exception): ...               # run_task while a task is running
class LLMUnavailable(Exception):              # infra retries exhausted / non-retryable error
    def __init__(self, detail: str): ...
class LLMInterrupted(Exception):              # stop or deadline hit while retrying
    def __init__(self, cause: Literal["operator", "task_time_limit"]): ...
```

### 6.7 Clock (`clock.py`)

```python
class Clock(Protocol):
    def now(self) -> float: ...               # seconds, monotonic

class MonotonicClock:
    def now(self) -> float: return time.monotonic()
```
