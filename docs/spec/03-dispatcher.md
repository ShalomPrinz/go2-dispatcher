# Go2 LLM Dispatcher v1 — Part 3 — Dispatcher

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

## 11. Context builder

Every LLM request is built from scratch from fixed slots. No conversation is carried. Each request has exactly one user message.

### 11.1 Request layout

| Order | Slot | Where | Changes |
|---|---|---|---|
| 1 | Output contract (S6) | `tools` (the `submit_plan` schema) | Never for a given config |
| 2 | System role and rules (S1) | `system[0]` | Never for a given config |
| 3 | Skill catalog (S2) | `system[1]` = `"## Skills\n" + catalog_text` | Never for a given config |
| 4 | Previous task (S7) | user message | Between tasks |
| 5 | Robot | user message | After steps |
| 6 | Task (S3) | user message | Never within a task |
| 7 | Budget | user message | After steps and calls |
| 8 | Executed so far (S4) | user message | After steps |
| 9 | Remaining plan (S5) | user message | After plans |
| 10 | Notice | user message | Per call |
| 11 | Rejection | user message | Schema retry only |

Static content comes first (the API processes tools, then system, then messages), so adding prompt caching later will not require reordering.

### 11.2 System text (S1)

`prompts.SYSTEM_TEMPLATE`, formatted with `{horizon}`. Draft wording (adjustable per OD-9; keep every rule):

```
You plan actions for a Unitree Go2 quadruped robot. An operator gives you a task in natural language. You answer by calling the submit_plan tool exactly once.

How the loop works:
- You return a plan: an ordered list of skill calls. The system runs the steps in order and then calls you again with the results.
- Each call is independent. Everything you know is in the message: the previous task, the robot's posture, the task, the remaining budget, the steps already executed, and any steps left over from your previous plan.
- A new plan replaces any previous plan. Steps listed under "Remaining plan" do not run unless you include them again.

Rules:
- Use only skills from the skill list, with only their declared parameters, and keep values inside the declared ranges.
- A plan may contain at most {horizon} steps.
- status PLAN: one or more steps to run. message is optional.
- status DONE: the task is complete. No steps. message is required: tell the operator what was done and anything they asked to find out.
- status ABORT: the task cannot or should not be done with these skills, or it is ambiguous. No steps. message is required: explain why, or ask the operator a clarifying question.
- replan_after N: stop after step N and call you again before running the rest. Use it when later steps depend on what earlier steps find. Omit it to run the whole plan.
- If a step fails, the rest of that plan is abandoned and you are called again with the failure.
- The motion budget limits total travel and total rotation for this task. A plan that would exceed it is rejected before any step runs.
```

### 11.3 User message format

Sections in this exact order and wording, separated by one blank line. Empty sections contain the literal placeholder, so the shape never changes.

```
## Previous task
{previous task block or "(none)"}

## Robot
Posture: {standing|sitting|unknown}

## Task
{task text, verbatim}

## Budget
Travel: {used} of {max} m used
Rotation: {used} of {max} deg used
Failures: {failures} of {max_failures}
Model calls: {calls_made} of {max_llm_calls}

## Executed so far
{entries or "(nothing yet)"}

## Remaining plan
{lines or "(none)"}

## Notice
{notice or "(none)"}
```

- Budget numbers: `round(x, 2)` then `:g`.
- `calls_made` = completed LLM calls **before** this request.
- The task text is inserted exactly as received (after the transport's `.strip()`), never summarised or rewritten.

### 11.4 Previous task (S7)

From the dispatcher's in-memory `TaskSummary` of the last finished task in this process (lost on restart → `(none)`):

```
Task: {task}
Outcome: {outcome code}
Message: {message}
Last step: {rendered step line without its number, or "(none)"}
```

### 11.5 Robot

`Posture:` is the dispatcher's last known posture, updated after every dispatched step:

1. If the step's response has a `state_after`, use its posture.
2. Else, if the step has a `stop_move` result whose response has a `state_after`, use that posture.
3. Else, if the step was killed (timeout or interrupted), set `unknown`.
4. Else keep the previous value.

Initial value at process start: stub → `stub.initial_posture` (it was just reset); real → from `executor.read_state()` at startup, or `unknown` if that fails. Carried across tasks.

### 11.6 Executed so far (S4) and the step renderer

`render.py` provides `render_step(sr: StepResult, registry, *, numbered: bool) -> str`, used by both the context and `format_outcome`.

Line formats:

```
{index}. {skill}({params}) -> ok{obs}
{index}. {skill}({params}) -> {outcome}: {error_message}
- rejected before running: {skill}({params}) -> {outcome}: {error_message}
```

- `{params}`: `name=value` pairs, joined with `", "`. For a known skill, declared params in frontmatter order (then any undeclared keys in received order); for an unknown skill, received order. Strings unquoted; numbers `:g`; booleans `true`/`false`; other values `json.dumps`.
- `{obs}`: for `ok`, the keys in `policy.context_observations` that are present in the response observations, as `": k=v, k=v"`; empty if none.
- `{error_message}`: one line, ≤ 200 characters (longer is cut to 199 + `…`). Never stderr, never a traceback.
- Non-dispatched entries (`rejected`, `motion_budget_exceeded`) use the `-` form.
- `numbered=False` drops the `{index}. ` prefix (used for the Previous task "Last step").

The section lists every recorded step of the current task in order. If there are more than K = `context_history_k` entries, the first line is `({n} earlier entries omitted)` followed by the last K entries. No summarisation.

### 11.7 Remaining plan (S5)

The steps of the **most recent plan** that did not run, each with its plan-local position and a tag:

```
{plan_step}. {skill}({params}) [pending]      # after a checkpoint
{plan_step}. {skill}({params}) [abandoned]    # after a failure or a rejection
```

- After a plan-level rejection (bounds or motion budget), **all** steps of the plan are listed as `[abandoned]`, including the rejected one (which also appears in Executed so far).
- After a failure during execution, the steps after the failed one are `[abandoned]`.
- After a plan runs to completion, and on the first call: `(none)`.
- Params rendered as in §11.6 (raw params, since these steps were not filled).

### 11.8 Fixed texts (`prompts.py`)

Keep all wording identical across experimental conditions. Draft wording (OD-9).

**Notices** (LLM-facing), chosen by return reason:

| Return reason | Notice |
|---|---|
| `initial` | `(none)` |
| `plan_complete` | `Your previous plan ran to completion. Return DONE with a message if the task is complete; otherwise plan the next steps.` |
| `checkpoint` | `You asked to review results after step {n} of your previous plan. Its remaining steps are listed under "Remaining plan"; include them again if you still want them.` |
| `failure` | `Your previous plan failed at step {n} ({skill}): {outcome}. This is failure {f} of {max_failures}. Revise the plan to avoid that failure, or return ABORT with a message if the task cannot be done.` |

`{n}` is the plan-local step (`plan_step`). Only `failure` notices show the failure count; the Budget section always shows it. A `schema_retry` request reuses the original request's user message byte-for-byte (including its notice) and appends the Rejection section.

**Rejection section** (appended last, only on a schema retry):

```
## Your previous reply was rejected
{one line per validation error}
Call submit_plan again with a corrected plan.
```

**Motion-budget message** (`error_message` for `motion_budget_exceeded`):

```
this step needs {need:g} {unit} of {kind} but only {left:g} {unit} remain for this task
```

`kind` = `travel` (unit `m`) or `rotation` (unit `deg`); numbers rounded to 2 decimals.

**Operator messages** (`TaskOutcome.message`; also shown in the next task's Previous task slot):

| Outcome | Message |
|---|---|
| `DONE` | the model's `message` |
| `ABORTED` | the model's `message` |
| `STOPPED` | `Stopped on request. A stop command was sent to the robot.` |
| `TIME_LIMIT_EXCEEDED` | `Stopped: the task exceeded its {limit:g}s time limit. A stop command was sent to the robot.` |
| `FAILURE_BUDGET_EXHAUSTED` | `Stopped: the robot failed {n} times while trying this task. Last failure: {skill}: {error_message}` |
| `CALL_BUDGET_EXHAUSTED` | `Stopped: the task reached the limit of {n} planning calls without finishing.` |
| `LLM_INVALID` | `Stopped: the model returned an invalid plan twice.` |
| `LLM_ERROR` | `Stopped: the model could not be reached ({detail}).` |
| `INTERNAL_ERROR` | `Stopped: internal error ({ExceptionType}). See run log {run_id}.` |

If `stop_move_failed`, append: ` WARNING: the stop command to the robot failed. Stop the robot manually.`

**Transport texts** (`prompts.py`, used by §16):

| Key | Text |
|---|---|
| `BUSY` | `Busy: a task is running. Send "stop" to stop it.` |
| `STOPPING` | `Stopping.` |
| `NOTHING_RUNNING` | `Nothing is running.` |
| `WORKING` | `Working on it.` |
| `EMPTY_TASK` | `Send a task, for example: walk forward one metre.` |
| `HELP` | `I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current task. Backend: {backend}.` |

---

## 12. LLM client and plan contract

### 12.1 Interfaces

```python
class LLMResult(BaseModel):
    plan: Plan | None
    tool_input: Any | None               # raw tool input as received
    errors: list[str]                    # empty iff plan is valid
    rejection_kind: Literal["none", "schema", "horizon", "semantic", "no_tool_call", "max_tokens"]
    horizon_exceeded: bool
    usage: dict                          # response.usage.model_dump(), verbatim
    stop_reason: str | None
    content: list[dict]                  # all response content blocks, model_dump()
    latency_ms: float                    # successful attempt only
    total_ms: float                      # whole plan() call, including failed attempts and backoff
    attempts: int                        # 1 + infra retries
    response_id: str | None
    request_id: str | None

class PlannerClient(Protocol):
    def plan(self, *, system: list[str], user: str, tool_schema: dict, call_index: int,
             remaining_s: Callable[[], float], stop_event: threading.Event,
             on_infra_retry: Callable[[dict], None]) -> LLMResult: ...

class AnthropicPlanner:
    def __init__(self, api_key: str, llm_cfg: LLMConfig, horizon: int, *,
                 http_client: httpx.Client | None = None,
                 wait: Callable[[threading.Event, float], bool] = lambda ev, s: ev.wait(s)): ...
```

- `plan()` returns an `LLMResult` for every completed API response, valid or not; validation (§13.1) happens inside `plan()`.
- It raises `LLMUnavailable(detail)` for a non-retryable API error or when retries are exhausted, and `LLMInterrupted(cause)` when a stop or the task deadline cuts retrying short.
- The dispatcher depends only on `PlannerClient`. Tests use `ScriptedPlanner` (§19.1).
- `wait` is injected so tests can record backoff without sleeping.

### 12.2 Tool schema (`llm.plan_tool_schema(horizon) -> dict`)

Hand-written rather than `Plan.model_json_schema()`, because the horizon must be injected and pydantic's `$defs`/`title` noise adds tokens. The schema's `required` lists are intentionally stricter than the pydantic model; they are the constants `TOOL_REQUIRED = ["status", "steps"]` and `STEP_REQUIRED = ["skill", "params"]`. A unit test asserts that the schema's property names equal `Plan`'s and `PlanStep`'s field names and that `required` equals those constants.

```json
{
  "name": "submit_plan",
  "description": "Submit your plan for the current task. Call this exactly once.",
  "input_schema": {
    "type": "object",
    "properties": {
      "status": {
        "type": "string",
        "enum": ["PLAN", "DONE", "ABORT"],
        "description": "PLAN: run the steps. DONE: task complete, no steps. ABORT: cannot or should not be done, no steps."
      },
      "steps": {
        "type": "array",
        "maxItems": HORIZON,
        "description": "Skill calls to run in order. Empty for DONE and ABORT.",
        "items": {
          "type": "object",
          "properties": {
            "skill": {"type": "string", "description": "Skill name from the skill list."},
            "params": {"type": "object", "description": "Parameter values for this skill."}
          },
          "required": ["skill", "params"],
          "additionalProperties": false
        }
      },
      "replan_after": {
        "type": "integer",
        "minimum": 1,
        "description": "Optional. Stop after this step number and call again before running the rest."
      },
      "message": {
        "type": "string",
        "description": "Required for DONE and ABORT: text for the operator. Optional for PLAN."
      }
    },
    "required": ["status", "steps"],
    "additionalProperties": false
  }
}
```

(`HORIZON` is the integer.)

### 12.3 API call

```python
client = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client)  # own retries
resp = client.with_options(timeout=attempt_timeout_s).messages.create(
    model=cfg.model,
    max_tokens=cfg.max_tokens,
    temperature=cfg.temperature,
    system=[{"type": "text", "text": system[0]}, {"type": "text", "text": system[1]}],
    tools=[tool_schema],
    tool_choice={"type": "tool", "name": "submit_plan"},
    messages=[{"role": "user", "content": user}],
)
```

- **Non-strict** tool use (no `"strict": true`). Strict mode does not support `maxItems` or numeric bounds and injects an extra system prompt, which would distort token measurements. Our validation is the enforcer.
- No prompt caching, no streaming, no extended thinking (forced `tool_choice` is incompatible with extended thinking).
- `attempt_timeout_s = min(cfg.request_timeout_s, remaining_s())`, computed before **each** attempt. If `remaining_s() <= 0` before an attempt → raise `LLMInterrupted("task_time_limit")`. If `stop_event` is set before an attempt → raise `LLMInterrupted("operator")`.

### 12.4 Response handling

1. `resp.stop_reason == "max_tokens"` → `rejection_kind="max_tokens"`, error `"reply was cut off; keep the plan shorter"`.
2. Take the first content block with `type == "tool_use"` and `name == "submit_plan"`. None → `rejection_kind="no_tool_call"`, error `"no submit_plan call in reply"`. More than one → use the first (all content is logged).
3. Validate `block.input` per §13.1.
4. Always fill `usage` (`resp.usage.model_dump()`), `stop_reason`, `content`, `latency_ms` (monotonic around the successful `create`), `total_ms`, `attempts`, `response_id` (`resp.id`), `request_id` (`getattr(resp, "_request_id", None)`).

### 12.5 Schema retry (once)

If an `LLMResult` has errors, the dispatcher makes exactly **one** retry: same system and tools, user message = the original user message (byte-identical) + `"\n\n"` + the Rejection section (§11.8). Return reason `schema_retry`. If the retry is also invalid, the task ends `LLM_INVALID`. The retry is an LLM call (counts toward `max_llm_calls`). Invalid replies never count as failures.

### 12.6 Infrastructure retries

Inside `AnthropicPlanner.plan()`:

- Retryable: `anthropic.APIConnectionError` (includes `APITimeoutError`) and `anthropic.APIStatusError` with `status_code` in `{408, 409, 429}` or `>= 500` (includes 529 overloaded).
- Up to `infra_max_retries` retries. Before retry `i` (0-based), `sleep_s = infra_backoff_s[i]`; if the error response has a numeric `retry-after` header, `sleep_s = min(max(sleep_s, retry_after), 30.0)`.
- If `sleep_s >= remaining_s()` → raise `LLMInterrupted("task_time_limit")`.
- Call `on_infra_retry({"call_index", "attempt", "error_type", "status_code", "attempt_latency_ms", "sleep_s"})`.
- `if wait(stop_event, sleep_s): raise LLMInterrupted("operator")`.
- Non-retryable errors, and exhausted retries → `LLMUnavailable(f"{type(e).__name__} {status_code or ''}".strip())`.
- Infra retries are not LLM calls and not failures.

---

## 13. Plan validation, bounds, motion budget

### 13.1 Plan-level validation (`validation.py`)

```python
def validate_tool_input(raw: Any, horizon: int) -> tuple[Plan | None, list[str], bool, str]:
    """Returns (plan, errors, horizon_exceeded, rejection_kind)."""
```

1. **Horizon** (on the raw input, before pydantic): if `raw` is a dict and `raw.get("steps")` is a list with `len > horizon` → error `"plan has {n} steps; the maximum is {horizon}"`, `horizon_exceeded = True`.
2. **Schema**: `Plan.model_validate(raw)`. Each pydantic error becomes one line `"{dotted loc}: {msg}"`.
3. **Semantic** (only if schema passed):
   - `PLAN` with zero steps → `"status PLAN needs at least one step"`.
   - `DONE`/`ABORT` with steps → `"status {s} must have no steps"`.
   - `DONE`/`ABORT` with missing or blank `message` → `"status {s} needs a message"`.
   - `replan_after` with status ≠ `PLAN` → `"replan_after is only allowed with status PLAN"`.
   - `replan_after` < 1 or > `len(steps)` → `"replan_after must be between 1 and {len(steps)}"`.
4. `plan` is returned only if there are no errors at all (including horizon).
5. `rejection_kind`: `horizon` if `horizon_exceeded`, else `schema` if schema errors, else `semantic`, else `none`.

Plans are **never truncated**.

### 13.2 Step bounds (`bounds.py`)

```python
def check_step(step: PlanStep, registry: Registry) -> tuple[dict | None, list[str]]:
    """Returns (filled_params, violations); filled_params is None when there are violations."""
```

Checks in order, collecting all violations for the step:

1. Skill exists → else `"unknown skill '{s}'; available: {', '.join(sorted names)}"` (stop checking this step).
2. No undeclared params → `"unknown parameter '{p}' for skill {s}"`.
3. Required params present → `"missing parameter '{p}' for skill {s}"`.
4. Type (no other coercion):
   - `number`: `int` or `float`, not `bool`, finite. Kept as received.
   - `integer`: `int` not `bool`; a `float` with an integral value (`2.0`) is accepted and **converted to `int`**.
   - `string`: `str`, non-empty after `.strip()`; the **stripped** value is kept.
   - `enum`: `str` whose `.strip().lower()` is in `values`; the **normalised** value is kept.
   - Violation: `"parameter '{p}' for skill {s} must be {expected}, got {value!r}"`.
5. Range: `"parameter '{p}' for skill {s} is {v:g}, outside {min:g} to {max:g}"` (or `"below the minimum {min:g}"` / `"above the maximum {max:g}"` when one side is missing).
6. Fill defaults for absent optional params.

The returned `filled_params` contain normalised values; these are what is dispatched, logged, and shown in context.

### 13.3 Whole-plan pre-check

```python
@dataclass
class PrecheckResult:
    rejection: StepResult | None
    filled: list[dict]            # filled params for every plan step; empty if rejected

def precheck(plan: Plan, stop_at: int, registry: Registry, budget: MotionBudget,
             call_index: int) -> PrecheckResult
```

1. Run `check_step` on **every** step of the plan, in order. At the first step with violations, return a rejection: `StepResult(index=None, plan_step=i, skill, params=<raw params>, outcome="rejected", error_code="bounds", error_message="; ".join(violations) cut to 200 chars)`.
2. If all pass, simulate the motion budget over plan steps `1..stop_at` (`stop_at = replan_after or len(steps)`), cumulatively in order, starting from the task's current usage, using a copy of the budget. At the first step that would exceed a limit, return a rejection with `outcome="motion_budget_exceeded"`, `error_code="motion_budget_exceeded"`, params = filled params, message from §11.8.
3. Otherwise return `rejection=None` and `filled`.

On a rejection no step of the plan runs. It counts as one failure (see OD-11) and the next call's return reason is `failure`. This guarantees that a plan either runs within limits or does not start: no partial motion happens because of a later bad step.

### 13.4 Motion budget (`budget.py`)

```python
class MotionBudget:
    def __init__(self, max_distance_m: float, max_rotation_deg: float): ...
    used_distance_m: float
    used_rotation_deg: float
    def would_exceed(self, cost: MotionCost) -> Literal["travel", "rotation"] | None
    def charge(self, cost: MotionCost) -> None
    def copy(self) -> "MotionBudget"
```

- One per task, starting at zero.
- `charge()` is called when a step is **dispatched** (just before its subprocess starts), however the step ends. Commanded motion may have happened even if the step errors or is killed.
- Non-dispatched steps are never charged.
- Exceeds when `used + cost > max + 1e-9`. Travel is checked before rotation.
- Each skill supplies its own cost through `POLICY.motion_cost()` (walk → distance, turn → rotation, others → zero).
- The budget bounds **commanded** motion, not measured motion.

---

## 14. Executor: subprocesses, timeouts, kill, StopMove

### 14.1 Interface

```python
class Executor:
    def __init__(self, cfg: Config, base_dir: Path): ...
    def run(self, skill: SkillDescriptor, params: dict, *, fault: str | None,
            timeout_s: float, remaining_task_s: float,
            stop_event: threading.Event) -> ExecResult
    def kill_current(self, cause: Literal["operator", "task_time_limit", "shutdown"]) -> bool
    def stop_move(self, reason: str) -> StopMoveResult
    def read_state(self) -> RobotState | None

class ExecResult(BaseModel):
    outcome: Literal["ok", "error", "timeout", "malformed", "interrupted"]
    interrupt_cause: Literal["operator", "task_time_limit", "shutdown"] | None = None
    response: SkillResponse | None = None
    exit_code: int | None = None
    pid: int | None = None
    duration_ms: float
    stderr_tail: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    stop_move: StopMoveResult | None = None
```

`remaining_task_s` is computed by the dispatcher from its own clock; the executor measures its own elapsed time with `time.monotonic()`.

### 14.2 Starting a process

```python
argv = [sys.executable, "-m", skill.entrypoint, json.dumps(params)]
env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}   # {"ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN"}
env.update({
    "PYTHONUNBUFFERED": "1",
    "GO2_BACKEND": cfg.robot.backend,
    "GO2_IFACE": cfg.robot.network_interface,
    "GO2_YOLO_WEIGHTS": str(cfg.robot.yolo_weights),
    "GO2_STUB_STATE_FILE": str(cfg.stub.state_file),
    "GO2_STUB_TIME_SCALE": repr(cfg.stub.time_scale),
    "GO2_STUB_DETECTIONS": json.dumps(cfg.stub.detections),
    "GO2_PARENT_PID": str(os.getpid()),
})
if fault: env["GO2_STUB_FAULT"] = fault
proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=PIPE, stderr=PIPE,
                        text=True, encoding="utf-8", errors="replace",
                        cwd=base_dir, env=env, start_new_session=True)
```

- One process per call, never reused.
- Two daemon reader threads drain stdout and stderr into buffers (prevents pipe deadlock). Keep all stdout; keep the last 2000 characters of stderr.
- Under `self._lock`: `self._current = proc`, `self._kill_cause = None`.
- The dispatcher decides `fault` (§15.3); the executor only passes it through.

### 14.3 Waiting

```python
t0 = time.monotonic()
while True:
    with self._lock:
        rc = proc.poll()
        if rc is not None:
            cause = self._kill_cause
            self._current = None
            self._kill_cause = None
            break
    elapsed = time.monotonic() - t0
    if stop_event.is_set():            self._kill_locked(proc, "operator")
    elif elapsed >= remaining_task_s:  self._kill_locked(proc, "task_time_limit")
    elif elapsed >= timeout_s:         self._kill_locked(proc, "step_timeout")
    time.sleep(0.05)
join reader threads (timeout 2 s each)
```

`_kill_locked(proc, cause)`: under `self._lock`, if `self._current is proc` and `proc.poll() is None` and no cause is set yet: set `self._kill_cause = cause`, then `os.killpg(proc.pid, signal.SIGKILL)` (ignore `ProcessLookupError`). The first cause to be recorded wins. `kill_current(cause)` does the same for whatever process is current and returns whether it killed one. Because `poll()` (which reaps) and `killpg` both happen under the lock and `_current` is cleared under the same lock, a reused PID can never be signalled.

After the loop:

- **Killed** (`cause` set): try to parse a response from stdout (keep it if valid, for the log only). Outcome: `step_timeout` → `timeout`, error code `timeout`, message `"killed after {timeout_s:g}s timeout"`; `operator` → `interrupted`, `stopped_by_operator`, `"stopped by operator"`; `task_time_limit` → `interrupted`, `task_time_limit`, `"task time limit reached"`; `shutdown` → `interrupted`, `shutdown`, `"dispatcher shutting down"`. Then `stop_move(reason=<cause, with step_timeout→"step_timeout">)` and attach it as `ExecResult.stop_move`.
- **Exited by itself**: take the **last non-empty stdout line**, `json.loads`, validate as `SkillResponse`, and require `response.skill == skill.name`. Any failure → `outcome="malformed"`, `error_code="malformed"`, `error_message=f"skill process exited with code {rc} without a valid response"`. Valid → `outcome = response.status`; `error_code`/`error_message` from `response.error`.

### 14.4 Stop path

`stop_move(reason)`:

- `argv = [sys.executable, "-m", "go2_skills.stop_move", "{}"]`, same env without `GO2_STUB_FAULT`, `start_new_session=True`.
- **Never** registered as `_current`, so `kill_current()` can never kill it.
- Waits up to `cfg.robot.stop_move_timeout_s`; on timeout it is killed.
- `ok = True` only if it exited with a valid response with `status == "ok"`. There is no retry loop.
- Returns `StopMoveResult` (including the response, whose `state_after` updates posture per §11.5).

StopMove is sent:

1. by the executor after it kills a step (step timeout, operator stop, task time limit, shutdown);
2. by the dispatcher when a task ends `STOPPED` or `TIME_LIMIT_EXCEEDED` while no step is running (idempotent safety measure);
3. by the dispatcher on `INTERNAL_ERROR` (after `kill_current("shutdown")`);
4. by `Dispatcher.shutdown()` if a task is still running after the wait.

It is not sent after normal step completion (skills already call `StopMove()`), nor for other outcomes.

`read_state()`: runs `go2_skills.read_state` like `stop_move` (not registered as current, timeout `read_state_timeout_s`); returns `response.state_after` or `None`.

### 14.5 If the dispatcher dies

- Skill processes started by this executor watch their parent (`GO2_PARENT_PID`). If the dispatcher disappears, motion loops break and send `StopMove()` themselves, and any skill still alive 2 s later exits (§7.4).
- Every normal exit path of the transports calls `Dispatcher.shutdown()` (§15.1, §16).
- The physical/remote e-stop remains the final safety measure (documented in `docs/safety.md`).

---

## 15. Dispatcher loop

### 15.1 Public interface

```python
class Dispatcher:
    def __init__(self, cfg: Config, registry: Registry, planner: PlannerClient,
                 executor: Executor, runlog_factory: RunLogFactory, *,
                 clock: Clock = MonotonicClock(), initial_posture: str = "unknown"): ...
    def run_task(self, task: str, *, source: str, sender_id: str | None = None) -> TaskOutcome
    def request_stop(self, source: str) -> Literal["stopping", "idle"]
    def is_busy(self) -> bool
    def shutdown(self, wait_s: float) -> None
    registry: Registry                 # read by transports for format_outcome
    cfg: Config
    previous: TaskSummary | None
    posture: str
```

- `run_task` acquires a non-blocking `threading.Lock` (`self._task_lock`); failure raises `BusyError` immediately (no LLM call). Empty text raises `ValueError` (transports filter it first).
- A second lock, `self._state_lock`, protects `self._phase` (`"idle" | "llm_call" | "step" | "between" | "ending"`), the stop event and the current run log.
- `request_stop(source)`: under `_state_lock`, if `_phase` is `"idle"` or `"ending"` → return `"idle"`. Otherwise set `self._stop_event`, write a `stop_requested` record (`source`, `during=_phase`) to the current run log, release the state lock, call `executor.kill_current("operator")`, return `"stopping"`. Safe from any thread; returns immediately.
- `shutdown(wait_s)`: if busy: `request_stop("shutdown")`, wait for the task lock to become free up to `wait_s`; if still busy: `executor.kill_current("shutdown")` then `executor.stop_move("shutdown")`. Idempotent.
- `initial_posture` is supplied by `build_dispatcher` (§16.1).

### 15.2 Per-task state

```python
run_id: str                       # uuid4().hex
task: str
t_start: float                    # clock.now()
deadline: float                   # t_start + task_time_limit_s
log: RunLog
steps: list[StepResult]
dispatched_count: int
failures: int
llm_calls: int
budget: MotionBudget
remaining: list[tuple[int, PlanStep]]            # for the Remaining plan section
remaining_tag: Literal["pending", "abandoned"] | None
return_reason: str
notice_args: dict                                # n, skill, outcome, f for the notice
last_failure: StepResult | None
stop_move_failed: bool
usage_totals: dict[str, int]
```

Helper: `remaining_s() = deadline - clock.now()`.

### 15.3 Loop (normative)

```python
def run_task(task, source, sender_id):
    if not task.strip(): raise ValueError
    if not self._task_lock.acquire(blocking=False): raise BusyError
    try:
        init per-task state; set phase "between"; log task_start
        return_reason = "initial"

        while True:
            if (o := self._check_interrupts()): return o          # stop / deadline, sends StopMove
            if llm_calls >= max_llm_calls: return end("CALL_BUDGET_EXHAUSTED")

            user = context.build(...)                             # §11
            res = self._call(user, return_reason)                 # phase llm_call; logs request/response;
                                                                  # llm_calls += 1 on any LLMResult
            if res is interrupted: return end("STOPPED" or "TIME_LIMIT_EXCEEDED", stop_move=True)
            if res is unavailable: return end("LLM_ERROR")
            if (o := self._check_interrupts()): return o          # stop may have arrived during the call

            if res.errors:
                log horizon_rejection if res.horizon_exceeded else plan_invalid
                if llm_calls >= max_llm_calls: return end("CALL_BUDGET_EXHAUSTED")
                res = self._call(user + "\n\n" + rejection_section(res.errors), "schema_retry",
                                 retry_of=return_reason)
                (same interrupted / unavailable / _check_interrupts handling)
                if res.errors:
                    log horizon_rejection or plan_invalid
                    return end("LLM_INVALID")

            plan = res.plan; stop_at = plan.replan_after or len(plan.steps)
            log plan (with stop_at)
            if plan.status == "DONE":  return end("DONE", message=plan.message)
            if plan.status == "ABORT": return end("ABORTED", message=plan.message)

            pre = precheck(plan, stop_at, registry, budget, call_index=llm_calls)
            if pre.rejection:
                record(pre.rejection)                              # appended to steps, logged as step_result
                failures += 1; last_failure = pre.rejection
                remaining = all plan steps; remaining_tag = "abandoned"
                notice_args = {n: rejection.plan_step, skill, outcome, f: failures}
                if failures >= max_failures: return end("FAILURE_BUDGET_EXHAUSTED")
                return_reason = "failure"; continue

            failed = False
            for i, step in enumerate(plan.steps[:stop_at], start=1):
                if (o := self._check_interrupts()): return o
                desc = registry.get(step.skill); params = pre.filled[i-1]
                cost = desc.policy.motion_cost(params); timeout = desc.policy.timeout_s(params)
                dispatched_count += 1
                fault = lookup cfg.stub.faults by step == dispatched_count (stub only)
                budget.charge(cost)
                set phase "step"; log step_start
                ex = executor.run(desc, params, fault=fault, timeout_s=timeout,
                                  remaining_task_s=remaining_s(), stop_event=self._stop_event)
                set phase "between"
                sr = StepResult(index=dispatched_count, call_index=llm_calls, plan_step=i, ...ex...)
                record(sr); update posture (§11.5)
                if ex.stop_move and not ex.stop_move.ok: stop_move_failed = True
                if sr.outcome == "interrupted":
                    return end("STOPPED" if cause in ("operator", "shutdown") else "TIME_LIMIT_EXCEEDED")
                    # StopMove already sent by the executor
                if sr.outcome in FAILURE_OUTCOMES:
                    failures += 1; last_failure = sr
                    remaining = plan steps i+1..len; remaining_tag = "abandoned"
                    notice_args = {n: i, skill, outcome, f: failures}
                    failed = True; break

            if failed:
                if failures >= max_failures: return end("FAILURE_BUDGET_EXHAUSTED")
                return_reason = "failure"
            elif stop_at < len(plan.steps):
                remaining = plan steps stop_at+1..len; remaining_tag = "pending"
                notice_args = {n: stop_at}; return_reason = "checkpoint"
            else:
                remaining = []; remaining_tag = None; return_reason = "plan_complete"
    except Exception as e:
        log exception (with traceback)
        self.executor.kill_current("shutdown"); smr = self.executor.stop_move("internal_error")
        return end("INTERNAL_ERROR", stop_move_result=smr)
    finally:
        with state lock: phase = "idle"; stop_event.clear()
        close run log; release task lock
```

`_check_interrupts()`: if `stop_event` is set → `end("STOPPED", stop_move=True)`; elif `remaining_s() <= 0` → `end("TIME_LIMIT_EXCEEDED", stop_move=True)`; else `None`. (No step is running when it is called.)

`end(outcome, message=None, stop_move=False, stop_move_result=None)`:

1. Set phase `"ending"` (under state lock).
2. If `stop_move`, call `executor.stop_move(reason)` (`operator` for STOPPED, `task_time_limit` for TIME_LIMIT_EXCEEDED); record its result (log `stop_move`), update posture from it, set `stop_move_failed` if not ok. Log every other StopMove result too (from executor kills and internal errors).
3. Build the operator message (§11.8).
4. Build `TaskOutcome`; log `task_end`; append the index line.
5. `self.previous = TaskSummary(task, outcome, message, last dispatched step)`.
6. Return the outcome.

Easy to get wrong:

- The failure counter increments for every failure outcome, including rejections and motion-budget rejections. Checkpoints and plan completions never increment it.
- `FAILURE_BUDGET_EXHAUSTED` is decided immediately after the failure that reaches `max_failures`; no extra LLM call.
- `CALL_BUDGET_EXHAUSTED` is checked before every call, including the schema retry.
- `replan_after == len(steps)` is a normal completion (`plan_complete`), not a checkpoint.
- A new plan always replaces `remaining`.
- The stop event is cleared when the task ends, so a stale stop never affects the next task.
- Every StopMove result that happens during a task is logged as a `stop_move` record.

---

## 16. Transports: CLI and Telegram

Transports contain no dispatcher logic.

### 16.1 Shared (`transports/__init__.py`)

```python
def load_config_and_env(config_path: Path | None, overrides: dict) -> Config
def build_dispatcher(cfg: Config, *, need_llm: bool, reset_stub: bool,
                     planner: PlannerClient | None = None) -> Dispatcher
```

`catalog` and `state` do not build a dispatcher: `catalog` uses `load_config_and_env` + `Registry.load` + `prompts`/`llm.plan_tool_schema`; `state` uses `load_config_and_env` + `Executor(cfg, base_dir).read_state()`. Neither takes the process lock.

`build_dispatcher` (used by `run`, `batch`, `go2-bot`):

1. `cfg` comes from `load_config_and_env` (config errors exit 2; `.env` loaded from the base dir).
2. (reserved)
3. Acquire the process lock (§5.4).
4. Load the registry; `RegistryError` exits 2.
5. If `reset_stub` and backend is stub: reset the stub state file.
6. Planner: the given `planner`, else if env `GO2_TEST_PLANNER=module:factory` is set, import it and call `factory()` (tests only; the test adds its helper folder to `PYTHONPATH`), else if `need_llm` construct `AnthropicPlanner` (missing `ANTHROPIC_API_KEY` → print `Missing ANTHROPIC_API_KEY.` and exit 2).
7. Executor; initial posture: stub → `stub.initial_posture` if reset, else read from the state file; real → `executor.read_state()` posture or `unknown` (print a warning if unavailable).
8. `RunLogFactory(cfg.log.dir, session_id=uuid4().hex)`.
9. Register `atexit.register(dispatcher.shutdown, 0)`.

`format_outcome(outcome: TaskOutcome, registry) -> str` (plain text, no markdown):

```
{OUTCOME}: {message}
Steps: {dispatched} run, {failures} failed
{render_step(...) for each recorded step, numbered as in §11.6}
```

Not truncated to K. If the whole text exceeds 4000 characters, drop the oldest step lines and insert `({n} earlier lines omitted)` after the `Steps:` line (Telegram's limit is 4096).

### 16.2 CLI (`go2-dispatch`)

```
go2-dispatch [--config PATH] [--backend stub|real] [--horizon N] [--fault STEP:KIND ...] run "TASK"
go2-dispatch [same options] batch TASKS_FILE
go2-dispatch [--config PATH] --reset-stub
go2-dispatch [--config PATH] catalog
go2-dispatch [--config PATH] [--backend stub|real] state
```

| Command | Lock | Reset stub | Needs API key | Behaviour |
|---|---|---|---|---|
| `run` | yes | yes | yes | One task; print `format_outcome`; exit 0 for `DONE`, 1 otherwise. |
| `batch` | yes | yes | yes | One task per line, skip blank lines and lines starting with `#`; sequential in one process (previous task and posture carry over; OD-6); print each outcome; exit 0. |
| `--reset-stub` | yes | yes | no | Reset and exit 0. Real backend → exit 2. |
| `catalog` | no | no | no | Print the system text, catalog, tool schema and registry hash; exit 0. |
| `state` | no | no | no | `executor.read_state()`; print JSON; exit 0, or 1 if unavailable. |

- `--backend`, `--horizon` override `robot.backend`, `loop.planning_horizon`. `--fault STEP:KIND` (repeatable) replaces `stub.faults`; with the real backend it is a config error (exit 2).
- `run_task` runs in a worker thread; the main thread waits with `thread.join(0.2)` in a loop so signals are handled promptly.
- First Ctrl+C (SIGINT) while a task runs: `dispatcher.request_stop("cli")`, print `Stopping...`. In `batch`, the batch is then aborted after the current task ends (exit 130).
- Second Ctrl+C, or SIGTERM: `dispatcher.shutdown(0)` (kills the skill, sends StopMove), exit 130 (SIGINT) / 143 (SIGTERM).

### 16.3 Telegram (`go2-bot [--config PATH]`)

Library: `python-telegram-bot` ≥ 21 (async), long polling.

```python
def build_application(dispatcher: Dispatcher, cfg: Config, token: str) -> Application:
    app = (ApplicationBuilder().token(token)
           .concurrent_updates(True)        # REQUIRED: otherwise "stop" cannot arrive during a task
           .post_stop(on_post_stop)
           .build())
    msg = filters.UpdateType.MESSAGE        # new messages only; ignore edited messages
    app.add_handler(CommandHandler("start", on_start, filters=msg))
    app.add_handler(CommandHandler("stop", on_stop, filters=msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & msg, on_text))
    app.bot_data["dispatcher"] = dispatcher; app.bot_data["cfg"] = cfg
    return app
```

`main()`: `build_dispatcher(need_llm=True, reset_stub=True)`; missing `TELEGRAM_BOT_TOKEN` → exit 2; warn if `allowed_user_ids` is empty; `build_application(...).run_polling()`.

Handlers (all use `update.effective_user` and `update.effective_message`):

- If `effective_user` or `effective_message` is `None` → ignore.
- Authorisation first: `effective_user.id not in cfg.telegram.allowed_user_ids` → no reply; print a warning line to stderr.
- `on_start` → `HELP`.
- `on_stop`, and `on_text` when `text.strip().lower() == "stop"` → `dispatcher.request_stop("telegram")`: `"stopping"` → `STOPPING`; `"idle"` → `NOTHING_RUNNING`. The final outcome reply comes from the task's own handler.
- `on_text` otherwise:
  - empty after strip → `EMPTY_TASK`;
  - `dispatcher.is_busy()` → `BUSY`;
  - else reply `WORKING`, then `outcome = await asyncio.to_thread(dispatcher.run_task, text.strip(), source="telegram", sender_id=str(user_id))`; `BusyError` (race) → `BUSY`; reply `format_outcome(outcome, registry)`.
- Replies are plain text (no `parse_mode`).
- Any exception in a handler: print traceback to stderr, reply `Error: {ExceptionType}`.
- `on_post_stop(app)`: `await asyncio.to_thread(dispatcher.shutdown, cfg.robot.stop_move_timeout_s + 5)`.

---

## 17. Run log

### 17.1 Files

- One file per task: `{log.dir}/{YYYYMMDDTHHMMSS}_{run_id[:8]}.jsonl` (local time at task start).
- Index: `{log.dir}/index.jsonl`, one line per finished task: `run_id`, `file`, `ts_start`, `task`, `source`, `outcome`, `condition`, `backend`, `planning_horizon`, `max_llm_calls`, `registry_hash`, `llm_calls`, `failures`, `steps_dispatched`, `input_tokens`, `output_tokens`, `duration_ms`.
- `session_id`: uuid4 hex generated by `build_dispatcher`, links tasks from one process.

### 17.2 Writer

```python
class RunLogFactory:
    def __init__(self, log_dir: Path, session_id: str): ...
    def open(self, run_id: str, t_start_mono: float) -> RunLog
    def append_index(self, row: dict) -> None

class RunLog:
    def write(self, type: str, **payload) -> None     # thread-safe
    def close(self) -> None
    path: Path
```

Every line is one JSON object:

```json
{"ts": "<ISO 8601 local time with offset>", "t_mono_ms": <ms since task start>, "session_id": "...",
 "run_id": "...", "seq": <0-based, per file>, "type": "<type>", ...payload}
```

`write()` holds a `threading.Lock` while assigning `seq`, serialising (`json.dumps(..., ensure_ascii=False, default=str)`), writing the line + `\n`, and calling `flush()`. A crash leaves a usable partial log. `stop_requested` is written from the transport thread, which is why the writer must be thread-safe.

### 17.3 Record types

| type | Payload |
|---|---|
| `task_start` | `task`, `source`, `sender_id`, `condition`, `config` (full dump, no secrets), `registry_hash`, `system_text`, `catalog_text`, `tool_schema`, `skills`, `previous_task`, `posture`, `versions` (`python`, `anthropic`, `pydantic`, `go2_dispatcher`), `git_commit` (`git rev-parse HEAD` in base dir, best effort, else null) |
| `llm_request` | `call_index`, `return_reason`, `retry_of` (for `schema_retry`: the reason being retried; else null), `user_text` |
| `llm_retry` | `call_index`, `attempt`, `error_type`, `status_code`, `attempt_latency_ms`, `sleep_s` |
| `llm_response` | `call_index`, `latency_ms`, `total_ms`, `attempts`, `stop_reason`, `usage` (verbatim), `content`, `response_id`, `request_id` |
| `llm_error` | `call_index`, `detail` (LLMUnavailable) |
| `llm_interrupted` | `call_index`, `cause` |
| `plan` | `call_index`, `plan` (dict), `stop_at` |
| `horizon_rejection` | `call_index`, `tool_input`, `steps_in_plan`, `horizon`, `errors` (all errors, including non-horizon ones) |
| `plan_invalid` | `call_index`, `tool_input`, `rejection_kind`, `errors` |
| `step_start` | `index`, `call_index`, `plan_step`, `skill`, `params`, `timeout_s`, `motion_cost`, `fault` |
| `step_result` | full `StepResult` (dispatched or not), `budget_used` (`distance_m`, `rotation_deg`), `failures`, `posture` |
| `stop_requested` | `source`, `during` (`llm_call` \| `step` \| `between`) |
| `stop_move` | full `StopMoveResult` |
| `exception` | `where`, `type`, `message`, `traceback` |
| `task_end` | `outcome`, `message`, `llm_calls`, `failures`, `steps_recorded`, `steps_dispatched`, `rejections`, `horizon_rejections`, `usage_totals` (sum over responses of every top-level numeric, non-null `usage` field), `budget_used`, `stop_move_failed`, `final_posture`, `duration_ms` |

### 17.4 What the log must allow computing

- Tokens per task and per call (`usage`).
- LLM latency per call (successful attempt and total, including retries).
- Skill wall time and process overhead (`duration_ms − response.timing.total_ms`).
- LLM calls per task broken down by `return_reason`; failures; rejections; horizon rejections (to monitor the reject-vs-truncate decision).
- The exact prompts sent (`task_start` system/catalog/tools + `user_text` per call).
- Robot state before/after every step and after every StopMove.
- The experimental condition (`condition`, `registry_hash`, `planning_horizon`, `max_llm_calls`).

---

## 18. Failure handling reference

| Situation | Behaviour | Failure? | LLM call? | Task ends |
|---|---|---|---|---|
| Config / registry error, missing key/token, lock held | Message, exit 2 | – | – | – |
| LLM transport error, retries left | Backoff, retry, `llm_retry` | no | no | no |
| Stop or deadline during LLM retries | `llm_interrupted` | no | no | `STOPPED` / `TIME_LIMIT_EXCEEDED` |
| Retries exhausted / non-retryable API error | `llm_error` | no | no | `LLM_ERROR` |
| No tool call / max_tokens / schema / horizon / semantic | One retry with Rejection section | no | yes | only if retry invalid → `LLM_INVALID` |
| Bounds violation anywhere in plan | Plan rejected before running | yes | – | if failures reach max |
| Motion budget would be exceeded | Plan rejected before running | yes | – | if failures reach max |
| Skill `status=error` | Rest of plan abandoned | yes | – | if failures reach max |
| Skill timeout | Kill + StopMove; rest abandoned | yes | – | if failures reach max |
| Skill output invalid / crash | Rest abandoned | yes | – | if failures reach max |
| Operator `stop` | Kill (if running) + StopMove | no | – | `STOPPED` |
| Task time limit | Kill (if running) + StopMove | no | – | `TIME_LIMIT_EXCEEDED` |
| StopMove fails | Warning appended to operator message | – | – | – |
| `max_llm_calls` reached | – | – | – | `CALL_BUDGET_EXHAUSTED` |
| Unhandled exception | Traceback logged; kill + StopMove | – | – | `INTERNAL_ERROR` |
| Dispatcher process dies | Skills detect orphaning, stop, exit | – | – | – |

Invariant: **nothing enters the LLM context that the dispatcher did not shape.** No tracebacks, stderr, raw stdout, or provider error text. Every problem reaches the model as one short sentence.
